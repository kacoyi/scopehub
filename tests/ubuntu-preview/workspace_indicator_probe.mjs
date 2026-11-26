import childProcess from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { appendFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { syncBuiltinESMExports } from 'node:module';
import { StringDecoder } from 'node:string_decoder';

const statusOperation = 'indicator_preview_status';
const indicatorOperations = new Set(['indicator.createDraft', 'indicator.runPreview', statusOperation, 'task.cancel']);
const statusHoldMs = 350;

function describe(frame) {
  return {
    operation: frame?.intent?.operation ?? frame?.operation ?? frame?.type,
    request_id: frame?.request_id, task_id: frame?.task_id,
  };
}

export function observeIndicatorRequests(page, endpoint) {
  const requests = new Map();
  const reading = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname !== endpoint) return;
    const payload = request.postDataJSON();
    if (!payload) return;
    const fields = describe(payload);
    if (indicatorOperations.has(fields.operation)) requests.set(request, { ...fields, payload, at_ms: Date.now() });
  });
  page.on('requestfailed', request => {
    const attempt = requests.get(request);
    if (attempt) attempt.failure = request.failure()?.errorText;
  });
  page.on('response', response => {
    const attempt = requests.get(response.request());
    if (!attempt) return;
    attempt.http = response.status();
    reading.push(response.json().then(reply => { attempt.reply = reply; }, error => {
      attempt.response_error = error.name;
    }));
  });
  return { get attempts() { return [...requests.values()]; }, settle: () => Promise.all(reading) };
}

export async function readIndicatorJournal(file) {
  const bytes = await readFile(file, 'utf8').catch(error => {
    if (error.code === 'ENOENT') return '';
    throw error;
  });
  // A live reader can see the start of the last append before its newline.
  return bytes.split('\n').slice(0, -1).map(JSON.parse);
}

// Installed only in the journey's Vite child. Record real Rust replies before
// the ordinary host sends them, and interrupt only the selected status delivery.
export function installIndicatorProbe(endpoint, writerPath, { journal, mode, refuse }) {
  const mark = (stage, fields) => appendFileSync(journal, JSON.stringify({ at_ms: Date.now(), stage, ...fields }) + '\n');
  const spawn = childProcess.spawn;
  const heldResponses = new Map();
  const lostRequests = new Set();
  const lossLimit = mode === 'lose-twice' ? 2 : Number(['lose-once', 'cancelled', 'deadline'].includes(mode));
  const holdMs = mode === 'deadline' || refuse ? 800 : statusHoldMs;
  let heldReady = false;

  childProcess.spawn = function (program, ...args) {
    const child = spawn.call(this, program, ...args);
    if (program !== writerPath) return child;
    const pid = child.pid;
    mark('writer-spawn', { pid });
    child.on('close', (exit, signal) => mark('writer-close', { pid, exit, signal }));
    const pending = [];
    const write = child.stdin.write;
    child.stdin.write = function (line, ...args) {
      let input;
      try { input = JSON.parse(String(line)); }
      catch { mark('pipe-input-invalid', { pid }); }
      const fields = describe(input?.command ?? input);
      pending.push(fields);
      if (indicatorOperations.has(fields.operation)) mark('pipe-write', { ...fields, pid, input });
      return write.call(this, line, ...args);
    };
    const decoder = new StringDecoder('utf8');
    let remainder = '';
    child.stdout.prependListener('data', bytes => {
      const lines = (remainder + decoder.write(bytes)).split('\n');
      remainder = lines.pop();
      for (const line of lines) {
        let output;
        try { output = JSON.parse(line); }
        catch { mark('pipe-reply-invalid', { pid }); continue; }
        if (output?.private_profile) continue;
        const fields = pending.shift();
        if (indicatorOperations.has(fields?.operation)) mark('pipe-reply', { ...fields, pid, output });
      }
    });
    return child;
  };
  syncBuiltinESMExports();

  return {
    plugin: {
      name: 'scopehub-test-indicator-delivery',
      enforce: 'pre',
      configureServer(server) {
        server.middlewares.use((request, response, next) => {
          if (request.url !== endpoint || request.method !== 'POST') return next();
          const chunks = [];
          let fields;
          request.on('data', bytes => chunks.push(bytes));
          request.once('end', () => {
            try { fields = describe(JSON.parse(Buffer.concat(chunks).toString())); }
            catch { /* The product handles invalid requests. */ }
          });
          const finish = stage => {
            if (indicatorOperations.has(fields?.operation)) mark(stage, {
              ...fields, http: response.statusCode, headers_sent: response.headersSent,
            });
          };
          response.once('finish', () => finish('http-finished'));
          response.once('close', () => {
            clearTimeout(heldResponses.get(response));
            heldResponses.delete(response);
            if (!response.writableFinished) finish('http-closed');
          });
          const { writeHead, end } = response;
          let head;
          response.writeHead = function (...args) {
            if (mode === 'observe' || fields?.operation !== statusOperation) return writeHead.apply(this, args);
            head = args;
            return this;
          };
          response.end = function (...args) {
            if (!indicatorOperations.has(fields?.operation)) return end.apply(this, args);
            let body;
            try { body = JSON.parse(args[0]); }
            catch { /* Preserve unrecognized host responses. */ }
            const http = head?.[0] ?? response.statusCode;
            mark('http-output', { ...fields, http, body });
            const release = () => {
              if (head) writeHead.apply(this, head);
              return end.apply(this, args);
            };
            if (!head || body?.result?.status !== 'ready') return release();

            const id = fields.request_id;
            const lose = lostRequests.has(id) || lostRequests.size < lossLimit;
            if (!lose && mode === 'cancelled') {
              const { protocol_version, stream_id, sequence, task_id } = body.result.event;
              const event = { protocol_version, stream_id, sequence: sequence + 1, task_id,
                message_id: randomUUID(), timestamp_ms: Date.now(), type: 'event', event: 'task.terminal',
                outcome: { status: 'cancelled' }, committed_effects: [] };
              const replacement = { ok: true, result: { status: 'terminal', task_id: fields.task_id, event } };
              mark('status-substituted', { ...fields, body: replacement });
              args[0] = JSON.stringify(replacement);
              return release();
            }
            if (!lose && mode === 'deadline') {
              heldResponses.set(response, undefined);
              mark('status-held', { ...fields, until: 'browser-deadline' });
              return this;
            }
            if (!lose && (mode !== 'hold-ready' || heldReady)) return release();
            if (lose) lostRequests.add(id);
            heldReady = true;
            const started = performance.now();
            mark('status-held', { ...fields, hold_ms: holdMs });
            heldResponses.set(response, setTimeout(() => {
              mark(lose ? 'status-lost' : 'status-released', {
                ...fields, headers_sent: response.headersSent, hold_ms: Math.round(performance.now() - started),
              });
              if (lose) response.destroy();
              else release();
            }, holdMs));
            return this;
          };
          next();
        });
      },
    },
    close() {
      childProcess.spawn = spawn;
      syncBuiltinESMExports();
      for (const [response, timer] of heldResponses) {
        clearTimeout(timer);
        response.destroy();
      }
    },
  };
}
