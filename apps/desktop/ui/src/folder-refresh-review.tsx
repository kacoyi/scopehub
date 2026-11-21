import { useState } from 'react';
import type {
  Bar, FolderRefreshBar, FolderRefreshFile, PrepareLocalFolderPublicationInput,
  PrepareLocalFolderPublicationOutput, PreviewLocalFolderRefreshOutput,
} from '../../../../contracts/generated/protocol';

const fileChange = {
  added: 'Added', unchanged: 'Unchanged', replaced: 'Replaced', removed: 'Removal candidate', unverified: 'Source unverified',
} satisfies Record<FolderRefreshFile['change'], string>;
const fileReview = {
  compatible: 'Compatible', header_changed: 'Header needs review', series_mismatch: 'Different series · Review required',
  invalid_rows: 'Rows need review', unavailable: 'Source unavailable · Review required', removed: 'Source absent · Review required',
} satisfies Record<FolderRefreshFile['review'], string>;
const barStatus = {
  unchanged: 'Same bar', addition: 'New bar', conflict: 'Competing values · Review required', withdrawal: 'Withdrawal candidate · Review required',
  unverified: 'Coverage unverified · Review required',
} satisfies Record<FolderRefreshBar['status'], string>;
const PAGE_SIZE = 20;

export type FolderPublicationChoices = Pick<PrepareLocalFolderPublicationInput, 'conflicts' | 'withdraw_bars' | 'withdraw_sources'>;

type PublicationControls = {
  busy: boolean;
  prepared: PrepareLocalFolderPublicationOutput | undefined;
  attempted: boolean;
  onPrepare: (choices: FolderPublicationChoices) => void;
  onPublish: () => void;
  onEdit: () => void;
};

function values(bar: Bar, quantityUnit: string) {
  const volume = bar.volume === null ? 'unknown' : `${bar.volume} ${quantityUnit}`;
  return `O ${bar.open} · H ${bar.high} · L ${bar.low} · C ${bar.close} · Volume ${volume} · ${bar.trading_day}`;
}

function toggle<T>(current: Set<T>, value: T, checked: boolean) {
  const next = new Set(current);
  if (checked) next.add(value);
  else next.delete(value);
  return next;
}

export function FolderRefreshReview({ revisionId, result: staged, onPreview, ...controls }: PublicationControls & {
  revisionId: string | undefined;
  result: PreviewLocalFolderRefreshOutput | undefined;
  onPreview: (revisionId: string) => void;
}) {
  const [draft, setDraft] = useState<{ base: string | undefined; value: string }>();
  const [page, setPage] = useState(0);
  const [changedOnly, setChangedOnly] = useState(true);
  const input = draft && draft.base === revisionId ? draft.value : revisionId ?? '';
  const id = input.trim().toLowerCase();
  const valid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(id);
  const result = staged && (staged.folder_base?.revision_id ?? staged.base?.revision_id) === id ? staged : undefined;

  return <section className="local-import-card" aria-label="Folder refresh review">
    <h2>Review folder refresh</h2>
    <form onSubmit={event => {
      event.preventDefault();
      if (valid && !controls.busy) {
        setPage(0);
        onPreview(id);
      }
    }}>
      <div className="local-import-fields"><label className="local-import-wide">Accepted revision ID
        <input value={input} maxLength={36} disabled={controls.busy}
          onChange={event => setDraft({ base: revisionId, value: event.currentTarget.value })} autoComplete="off" spellCheck={false} />
      </label></div>
      <button type="submit" className="secondary-button" disabled={controls.busy || !valid}>Stage folder refresh</button>
    </form>
    {result && <FolderComparison key={result.review_id ?? id} result={result} page={page} setPage={setPage}
      changedOnly={changedOnly} setChangedOnly={setChangedOnly} {...controls} />}
  </section>;
}

function FolderComparison({ result, page, setPage, changedOnly, setChangedOnly, busy, prepared, attempted, onPrepare, onPublish, onEdit }:
  PublicationControls & {
    result: PreviewLocalFolderRefreshOutput;
    page: number;
    setPage: (value: number) => void;
    changedOnly: boolean;
    setChangedOnly: (value: boolean) => void;
  }) {
  const [choices, setChoices] = useState(new Map<number, number>());
  const [withdrawBars, setWithdrawBars] = useState(new Set<number>());
  const [withdrawSources, setWithdrawSources] = useState(new Set<string>());
  const { base, folder_base: folderBase } = result;
  const currentBase = folderBase ?? base;
  if (!currentBase) return null;
  const changeable = Boolean(result.review_id && result.publication_allowed);
  const choicesDisabled = busy || prepared !== undefined;
  const bars = result.bars.filter(bar => !changedOnly || bar.status !== 'unchanged');
  const conflicts = result.bars.filter(bar => bar.status === 'conflict');
  const barWithdrawals = result.bars.filter(bar => bar.status === 'withdrawal');
  const sourceWithdrawals = result.files.filter(file => file.change === 'removed');
  const reviewed = choices.size === conflicts.length
    && withdrawBars.size === barWithdrawals.length && withdrawSources.size === sourceWithdrawals.length;
  const acceptedPaths = new Map<string, string>();
  for (const file of result.files) {
    if (file.prior_sha256 && !acceptedPaths.has(file.prior_sha256)) {
      acceptedPaths.set(file.prior_sha256, file.relative_path);
    }
  }

  return <>
    <p role="status">Read-only preview against {currentBase.symbol} · {'origin_path' in currentBase ? currentBase.origin_path : currentBase.relative_path} · {currentBase.bar_count.toLocaleString()} accepted bars.</p>
    <p className="local-import-muted">
      Venue {currentBase.series.venue} · Price adjustment {currentBase.series.price_adjustment} · Quantity unit {currentBase.series.quantity_unit}
    </p>
    <div className="local-import-hash"><strong>Compared revision</strong><code>{currentBase.revision_id}</code></div>
    <div className="local-import-table table-scroll" tabIndex={0} role="region" aria-label="Scroll folder source changes">
      <table aria-label="Folder source changes">
        <thead><tr><th scope="col">Source</th><th scope="col">Change</th><th scope="col">Review</th></tr></thead>
        <tbody>{result.files.map(file => {
          const acceptedPath = file.source_sha256 ? acceptedPaths.get(file.source_sha256) : undefined;
          const historicalMatch = acceptedPath && acceptedPath !== file.relative_path && acceptedPath !== file.alias_of;
          return <tr key={file.relative_path}>
            <th scope="row">{file.relative_path}</th>
            <td>{fileChange[file.change]}
              {file.alias_of && <> · Alias of {file.alias_of} (current source)</>}
              {historicalMatch && <> · Alias of {acceptedPath} (accepted source)</>}
              {(file.prior_sha256 || file.source_sha256) && <details><summary>Source fingerprints</summary>
                {file.prior_sha256 && <p>Accepted <code>{file.prior_sha256}</code></p>}
                {file.source_sha256 && <p>Current <code>{file.source_sha256}</code></p>}
              </details>}</td>
            <td>{fileReview[file.review]}{file.detail && <p>{file.detail}</p>}
              {changeable && file.change === 'removed' && <label className="local-folder-choice">
                <input type="checkbox" disabled={choicesDisabled} checked={withdrawSources.has(file.relative_path)}
                  onChange={event => setWithdrawSources(toggle(withdrawSources, file.relative_path, event.currentTarget.checked))} />
                Withdraw source {file.relative_path}
              </label>}
            </td>
          </tr>;
        })}</tbody>
      </table>
    </div>
    <h3>Bar comparison</h3>
    <label><input type="checkbox" checked={!changedOnly} onChange={event => {
      setChangedOnly(!event.currentTarget.checked); setPage(0);
    }} /> Include unchanged overlaps</label>
    {bars.length === 0 && <p>No bar changes in the compatible sources.</p>}
    {bars.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map(item => {
      const time = new Date(item.time_ms).toISOString();
      const accepted = item.accepted;
      return <details key={item.time_ms}>
        <summary>{time} · {barStatus[item.status]}</summary>
        {accepted && <>
          <p>Accepted · {values(accepted.bar, currentBase.series.quantity_unit)}
            {'locators' in accepted && base && ` · ${base.relative_path}`}</p>
          <ul className="local-import-issues">{'locators' in accepted
            ? accepted.locators.map(locator => <li key={locator.record_number}>
              Accepted record {locator.record_number} · Lines {locator.line_start}–{locator.line_end}
            </li>)
            : accepted.sources.map(source => <li key={`${source.relative_path}:${source.record_number}`}>
              Accepted {source.relative_path} · Record {source.record_number} · Lines {source.line_start}–{source.line_end}
            </li>)}</ul>
        </>}
        {item.alternatives.map((entry, index) => <div key={index}>
          <p>Current · {values(entry.bar, currentBase.series.quantity_unit)}</p>
          <ul className="local-import-issues">{entry.sources.map(source => <li key={`${source.relative_path}:${source.record_number}`}>
            {source.relative_path} · Record {source.record_number} · Lines {source.line_start}–{source.line_end}
          </li>)}</ul>
          {changeable && item.status === 'conflict' && <label className="local-folder-choice">
            <input type="radio" name={`folder-conflict-${item.time_ms}`} checked={choices.get(item.time_ms) === index}
              disabled={choicesDisabled} onChange={() => setChoices(previous => new Map(previous).set(item.time_ms, index))} />
            Use alternative {index + 1} for {time}
          </label>}
        </div>)}
        {changeable && item.status === 'withdrawal' && <label className="local-folder-choice">
          <input type="checkbox" disabled={choicesDisabled} checked={withdrawBars.has(item.time_ms)}
            onChange={event => setWithdrawBars(toggle(withdrawBars, item.time_ms, event.currentTarget.checked))} />
          Withdraw bar at {time}
        </label>}
      </details>;
    })}
    {bars.length > PAGE_SIZE && <div className="local-publication-actions">
      <button type="button" className="secondary-button" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous bars</button>
      <button type="button" className="secondary-button" disabled={(page + 1) * PAGE_SIZE >= bars.length} onClick={() => setPage(page + 1)}>Next bars</button>
    </div>}
    {prepared ? <fieldset className="local-import-card local-access-confirmation" disabled={busy}>
      <legend>Publish reviewed folder?</legend>
      <p>The new frozen revision will contain {prepared.bar_count.toLocaleString()} {prepared.bar_count === 1 ? 'bar' : 'bars'} from {prepared.source_count.toLocaleString()} {prepared.source_count === 1 ? 'source' : 'sources'}.</p>
      <div className="local-publication-actions">
        <button type="button" className="primary" onClick={onPublish}>{attempted ? 'Retry folder publication' : 'Publish reviewed folder'}</button>
        <button type="button" className="secondary-button" onClick={onEdit}>Edit folder choices</button>
      </div>
    </fieldset> : changeable ? <div className="local-folder-publication">
      <p>{choices.size} of {conflicts.length} conflicts resolved · {withdrawBars.size} of {barWithdrawals.length} bar withdrawals reviewed · {withdrawSources.size} of {sourceWithdrawals.length} source withdrawals reviewed</p>
      <div className="local-publication-actions">
        {barWithdrawals.length > 1 && <button type="button" className="secondary-button" disabled={busy || withdrawBars.size === barWithdrawals.length}
          onClick={() => setWithdrawBars(new Set(barWithdrawals.map(bar => bar.time_ms)))}>Confirm all bar withdrawals</button>}
        {sourceWithdrawals.length > 1 && <button type="button" className="secondary-button" disabled={busy || withdrawSources.size === sourceWithdrawals.length}
          onClick={() => setWithdrawSources(new Set(sourceWithdrawals.map(file => file.relative_path)))}>Confirm all source withdrawals</button>}
        <button type="button" className="secondary-button" disabled={busy || !reviewed} onClick={() => onPrepare({
          conflicts: Array.from(choices, ([time_ms, alternative_index]) => ({ time_ms, alternative_index })).sort((left, right) => left.time_ms - right.time_ms),
          withdraw_bars: barWithdrawals.map(bar => bar.time_ms),
          withdraw_sources: sourceWithdrawals.map(file => file.relative_path),
        })}>Review folder publication</button>
      </div>
    </div> : result.publication_allowed === false && <p>Review the held sources and stage the folder again before publishing.</p>}
  </>;
}
