import { useState } from 'react';
import type {
  Bar, FolderRefreshBar, FolderRefreshFile, PrepareLocalFolderPublicationInput,
  PrepareLocalFolderPublicationOutput, PreviewLocalFolderRefreshOutput,
} from '../../../../contracts/generated/protocol';
import { formatNumber, plural, t } from './i18n';
import { folderRefreshFileDetail } from './local-import-transport';
import { formatSourceBarTime, formatSourceBarValue } from './local-preview-chart';

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
  const volume = bar.volume === null ? t('unknown') : `${formatSourceBarValue(bar.volume)} ${quantityUnit}`;
  return t('O {open} · H {high} · L {low} · C {close} · Volume {volume} · {day}', {
    open: formatSourceBarValue(bar.open), high: formatSourceBarValue(bar.high),
    low: formatSourceBarValue(bar.low), close: formatSourceBarValue(bar.close), volume, day: bar.trading_day,
  });
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

  return <section className="local-import-card" aria-label={t('Folder refresh review')}>
    <h2>{t('Review folder refresh')}</h2>
    <form onSubmit={event => {
      event.preventDefault();
      if (valid && !controls.busy) {
        setPage(0);
        onPreview(id);
      }
    }}>
      <div className="local-import-fields"><label className="local-import-wide">{t('Accepted revision ID')}
        <input value={input} maxLength={36} disabled={controls.busy}
          onChange={event => setDraft({ base: revisionId, value: event.currentTarget.value })} autoComplete="off" spellCheck={false} />
      </label></div>
      <button type="submit" className="secondary-button" disabled={controls.busy || !valid}>{t('Stage folder refresh')}</button>
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
    <p role="status">{t('Read-only preview against {symbol} · {path} · {count} accepted bars.', {
      symbol: currentBase.symbol, path: 'origin_path' in currentBase ? currentBase.origin_path : currentBase.relative_path,
      count: formatNumber(currentBase.bar_count),
    })}</p>
    <p className="local-import-muted">
      {t('Venue {venue} · Price adjustment {adjustment} · Quantity unit {unit}', {
        venue: currentBase.series.venue, adjustment: currentBase.series.price_adjustment, unit: currentBase.series.quantity_unit,
      })}
    </p>
    <div className="local-import-hash"><strong>{t('Compared revision')}</strong><code>{currentBase.revision_id}</code></div>
    <div className="local-import-table table-scroll" tabIndex={0} role="region" aria-label={t('Scroll folder source changes')}>
      <table aria-label={t('Folder source changes')}>
        <thead><tr><th scope="col">{t('Source')}</th><th scope="col">{t('Change')}</th><th scope="col">{t('Review')}</th></tr></thead>
        <tbody>{result.files.map(file => {
          const acceptedPath = file.source_sha256 ? acceptedPaths.get(file.source_sha256) : undefined;
          const historicalMatch = acceptedPath && acceptedPath !== file.relative_path && acceptedPath !== file.alias_of;
          return <tr key={file.relative_path}>
            <th scope="row">{file.relative_path}</th>
            <td>{t(fileChange[file.change])}
              {file.alias_of && <> · {t('Alias of {path} (current source)', { path: file.alias_of })}</>}
              {historicalMatch && <> · {t('Alias of {path} (accepted source)', { path: acceptedPath })}</>}
              {(file.prior_sha256 || file.source_sha256) && <details><summary>{t('Source fingerprints')}</summary>
                {file.prior_sha256 && <p>{t('Accepted')}{' '}<code>{file.prior_sha256}</code></p>}
                {file.source_sha256 && <p>{t('Current', undefined, 'sourceFingerprint')}{' '}<code>{file.source_sha256}</code></p>}
              </details>}</td>
            <td>{t(fileReview[file.review])}{file.detail && <p>{folderRefreshFileDetail(file.detail)}</p>}
              {changeable && file.change === 'removed' && <label className="local-folder-choice">
                <input type="checkbox" disabled={choicesDisabled} checked={withdrawSources.has(file.relative_path)}
                  onChange={event => setWithdrawSources(toggle(withdrawSources, file.relative_path, event.currentTarget.checked))} />
                {t('Withdraw source {path}', { path: file.relative_path })}
              </label>}
            </td>
          </tr>;
        })}</tbody>
      </table>
    </div>
    <h3>{t('Bar comparison')}</h3>
    <label><input type="checkbox" checked={!changedOnly} onChange={event => {
      setChangedOnly(!event.currentTarget.checked); setPage(0);
    }} /> {t('Include unchanged overlaps')}</label>
    {bars.length === 0 && <p>{t('No bar changes in the compatible sources.')}</p>}
    {bars.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map(item => {
      const time = formatSourceBarTime(item.time_ms);
      const accepted = item.accepted;
      return <details key={item.time_ms}>
        <summary>{time} · {t(barStatus[item.status])}</summary>
        {accepted && <>
          <p>{t('Accepted · {values}', { values: values(accepted.bar, currentBase.series.quantity_unit) })}
            {'locators' in accepted && base && ` · ${base.relative_path}`}</p>
          <ul className="local-import-issues">{'locators' in accepted
            ? accepted.locators.map(locator => <li key={locator.record_number}>
              {t('Accepted record {record} · Lines {start}–{end}', {
                record: formatNumber(locator.record_number, { useGrouping: false }),
                start: formatNumber(locator.line_start, { useGrouping: false }),
                end: formatNumber(locator.line_end, { useGrouping: false }),
              })}
            </li>)
            : accepted.sources.map(source => <li key={`${source.relative_path}:${source.record_number}`}>
              {t('Accepted {path} · Record {record} · Lines {start}–{end}', {
                path: source.relative_path, record: formatNumber(source.record_number, { useGrouping: false }),
                start: formatNumber(source.line_start, { useGrouping: false }),
                end: formatNumber(source.line_end, { useGrouping: false }),
              })}
            </li>)}</ul>
        </>}
        {item.alternatives.map((entry, index) => <div key={index}>
          <p>{t('Current · {values}', { values: values(entry.bar, currentBase.series.quantity_unit) })}</p>
          <ul className="local-import-issues">{entry.sources.map(source => <li key={`${source.relative_path}:${source.record_number}`}>
            {t('{path} · Record {record} · Lines {start}–{end}', {
              path: source.relative_path, record: formatNumber(source.record_number, { useGrouping: false }),
              start: formatNumber(source.line_start, { useGrouping: false }),
              end: formatNumber(source.line_end, { useGrouping: false }),
            })}
          </li>)}</ul>
          {changeable && item.status === 'conflict' && <label className="local-folder-choice">
            <input type="radio" name={`folder-conflict-${item.time_ms}`} checked={choices.get(item.time_ms) === index}
              disabled={choicesDisabled} onChange={() => setChoices(previous => new Map(previous).set(item.time_ms, index))} />
            {t('Use alternative {index} for {time}', { index: formatNumber(index + 1, { useGrouping: false }), time })}
          </label>}
        </div>)}
        {changeable && item.status === 'withdrawal' && <label className="local-folder-choice">
          <input type="checkbox" disabled={choicesDisabled} checked={withdrawBars.has(item.time_ms)}
            onChange={event => setWithdrawBars(toggle(withdrawBars, item.time_ms, event.currentTarget.checked))} />
          {t('Withdraw bar at {time}', { time })}
        </label>}
      </details>;
    })}
    {bars.length > PAGE_SIZE && <div className="local-publication-actions">
      <button type="button" className="secondary-button" disabled={page === 0} onClick={() => setPage(page - 1)}>{t('Previous bars')}</button>
      <button type="button" className="secondary-button" disabled={(page + 1) * PAGE_SIZE >= bars.length} onClick={() => setPage(page + 1)}>{t('Next bars')}</button>
    </div>}
    {prepared ? <fieldset className="local-import-card local-access-confirmation" disabled={busy}>
      <legend>{t('Publish reviewed folder?')}</legend>
      <p>{t('The new frozen revision will contain {bars} from {sources}.', {
        bars: plural('{count} bar', '{count} bars', prepared.bar_count),
        sources: plural('{count} source', '{count} sources', prepared.source_count),
      })}</p>
      <div className="local-publication-actions">
        <button type="button" className="primary" onClick={onPublish}>{attempted ? t('Retry folder publication') : t('Publish reviewed folder')}</button>
        <button type="button" className="secondary-button" onClick={onEdit}>{t('Edit folder choices')}</button>
      </div>
    </fieldset> : changeable ? <div className="local-folder-publication">
      <p>{t('{resolved} of {conflicts} conflicts resolved · {barsReviewed} of {barWithdrawals} bar withdrawals reviewed · {sourcesReviewed} of {sourceWithdrawals} source withdrawals reviewed', {
        resolved: formatNumber(choices.size, { useGrouping: false }), conflicts: formatNumber(conflicts.length, { useGrouping: false }),
        barsReviewed: formatNumber(withdrawBars.size, { useGrouping: false }), barWithdrawals: formatNumber(barWithdrawals.length, { useGrouping: false }),
        sourcesReviewed: formatNumber(withdrawSources.size, { useGrouping: false }), sourceWithdrawals: formatNumber(sourceWithdrawals.length, { useGrouping: false }),
      })}</p>
      <div className="local-publication-actions">
        {barWithdrawals.length > 1 && <button type="button" className="secondary-button" disabled={busy || withdrawBars.size === barWithdrawals.length}
          onClick={() => setWithdrawBars(new Set(barWithdrawals.map(bar => bar.time_ms)))}>{t('Confirm all bar withdrawals')}</button>}
        {sourceWithdrawals.length > 1 && <button type="button" className="secondary-button" disabled={busy || withdrawSources.size === sourceWithdrawals.length}
          onClick={() => setWithdrawSources(new Set(sourceWithdrawals.map(file => file.relative_path)))}>{t('Confirm all source withdrawals')}</button>}
        <button type="button" className="secondary-button" disabled={busy || !reviewed} onClick={() => onPrepare({
          conflicts: Array.from(choices, ([time_ms, alternative_index]) => ({ time_ms, alternative_index })).sort((left, right) => left.time_ms - right.time_ms),
          withdraw_bars: barWithdrawals.map(bar => bar.time_ms),
          withdraw_sources: sourceWithdrawals.map(file => file.relative_path),
        })}>{t('Review folder publication')}</button>
      </div>
    </div> : result.publication_allowed === false && <p>{t('Review the held sources and stage the folder again before publishing.')}</p>}
  </>;
}
