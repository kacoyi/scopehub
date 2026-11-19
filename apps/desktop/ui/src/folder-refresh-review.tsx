import { useState } from 'react';
import type { Bar, FolderRefreshBar, FolderRefreshFile, PreviewLocalFolderRefreshOutput } from '../../../../contracts/generated/protocol';

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

function values(bar: Bar, quantityUnit: string) {
  const volume = bar.volume === null ? 'unknown' : `${bar.volume} ${quantityUnit}`;
  return `O ${bar.open} · H ${bar.high} · L ${bar.low} · C ${bar.close} · Volume ${volume} · ${bar.trading_day}`;
}

export function FolderRefreshReview({ revisionId, busy, result: staged, onPreview }: {
  revisionId: string | undefined;
  busy: boolean;
  result: PreviewLocalFolderRefreshOutput | undefined;
  onPreview: (revisionId: string) => void;
}) {
  const [draft, setDraft] = useState<string>();
  const [page, setPage] = useState(0);
  const [changedOnly, setChangedOnly] = useState(true);
  const id = (draft ?? revisionId ?? '').trim().toLowerCase();
  const valid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(id);
  const result = staged?.base.revision_id === id ? staged : undefined;
  const bars = result?.bars.filter(bar => !changedOnly || bar.status !== 'unchanged') ?? [];
  const acceptedSourcePresent = result?.files.some(file =>
    file.relative_path === result.base.relative_path && file.source_sha256 === result.base.source_sha256);

  return <section className="local-import-card" aria-label="Folder refresh review">
    <h2>Review folder refresh</h2>
    <form onSubmit={event => {
      event.preventDefault();
      if (valid && !busy) {
        setPage(0);
        onPreview(id);
      }
    }}>
      <div className="local-import-fields"><label className="local-import-wide">Accepted revision ID
        <input value={draft ?? revisionId ?? ''} maxLength={36} disabled={busy}
          onChange={event => setDraft(event.currentTarget.value)} autoComplete="off" spellCheck={false} />
      </label></div>
      <button type="submit" className="secondary-button" disabled={busy || !valid}>Stage folder refresh</button>
    </form>
    {result && <>
      <p role="status">Read-only preview against {result.base.symbol} · {result.base.relative_path} · {result.base.bar_count.toLocaleString()} accepted bars.</p>
      <p className="local-import-muted">
        Venue {result.base.series.venue} · Price adjustment {result.base.series.price_adjustment} · Quantity unit {result.base.series.quantity_unit}
      </p>
      <div className="local-import-hash"><strong>Compared revision</strong><code>{result.base.revision_id}</code></div>
      <div className="local-import-table table-scroll" tabIndex={0} role="region" aria-label="Scroll folder source changes">
        <table aria-label="Folder source changes">
          <thead><tr><th scope="col">Source</th><th scope="col">Change</th><th scope="col">Review</th></tr></thead>
          <tbody>{result.files.map(file => <tr key={file.relative_path}>
            <th scope="row">{file.relative_path}</th>
            <td>{fileChange[file.change]}{file.alias_of && <> · Alias of {file.alias_of}
              {!acceptedSourcePresent && file.source_sha256 === result.base.source_sha256 && ' (accepted source)'}</>}
              {(file.prior_sha256 || file.source_sha256) && <details><summary>Source fingerprints</summary>
                {file.prior_sha256 && <p>Accepted <code>{file.prior_sha256}</code></p>}
                {file.source_sha256 && <p>Current <code>{file.source_sha256}</code></p>}
              </details>}</td>
            <td>{fileReview[file.review]}{file.detail && <p>{file.detail}</p>}</td>
          </tr>)}</tbody>
        </table>
      </div>
      <h3>Bar comparison</h3>
      <label><input type="checkbox" checked={!changedOnly} onChange={event => {
        setChangedOnly(!event.currentTarget.checked); setPage(0);
      }} /> Include unchanged overlaps</label>
      {bars.length === 0 && <p>No bar changes in the compatible sources.</p>}
      {bars.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map(item => <details key={item.time_ms}>
        <summary>{new Date(item.time_ms).toISOString()} · {barStatus[item.status]}</summary>
        {item.accepted && <>
          <p>Accepted · {values(item.accepted.bar, result.base.series.quantity_unit)} · {result.base.relative_path}</p>
          <ul className="local-import-issues">{item.accepted.locators.map(locator => <li key={locator.record_number}>
            Accepted record {locator.record_number} · Lines {locator.line_start}–{locator.line_end}
          </li>)}</ul>
        </>}
        {item.alternatives.map((entry, index) => <div key={index}>
          <p>Current · {values(entry.bar, result.base.series.quantity_unit)}</p>
          <ul className="local-import-issues">{entry.sources.map(source => <li key={`${source.relative_path}:${source.record_number}`}>
            {source.relative_path} · Record {source.record_number} · Lines {source.line_start}–{source.line_end}
          </li>)}</ul>
        </div>)}
      </details>)}
      {bars.length > PAGE_SIZE && <div className="local-publication-actions">
        <button type="button" className="secondary-button" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous bars</button>
        <button type="button" className="secondary-button" disabled={(page + 1) * PAGE_SIZE >= bars.length} onClick={() => setPage(page + 1)}>Next bars</button>
      </div>}
    </>}
  </section>;
}
