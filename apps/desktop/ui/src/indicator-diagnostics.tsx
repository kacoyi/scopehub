import type { Diagnostic, IndicatorResult } from '../../../../contracts/generated/protocol';
import { t } from './i18n';

const builtInEngines = new Set(['scopehub-declarative-1', 'scopehub-declarative-2', 'scopehub-declarative-3']);

function diagnosticText(diagnostic: Diagnostic, engine: string) {
  if (!builtInEngines.has(engine)) return diagnostic.message;
  switch (diagnostic.code) {
    case 'session_schedule_applied': return t("The source's scheduled closures add no indicator buckets.");
    case 'missing_expected_buckets': return t('One or more expected market buckets are missing. Affected windows are unknown.');
    case 'calendar_unverified': return t('This source has no verified session schedule. Indicator windows restart after timestamp gaps.');
    case 'division_by_zero': return t('Division by zero produced unknown values.');
    default: return diagnostic.message;
  }
}

export function IndicatorDiagnostics({ result }: { result: IndicatorResult }) {
  return <>{result.diagnostics.map((diagnostic, index) => <p key={`${diagnostic.code}:${index}`}>
    {diagnosticText(diagnostic, result.provenance.engine_version)}
  </p>)}</>;
}
