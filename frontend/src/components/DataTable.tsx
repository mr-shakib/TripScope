import type { Granularity, SeriesPoint, Unit } from "../api/types";
import { formatBucket, formatInteger, formatValue } from "../lib/format";

/** The chart's table twin: every plotted value is reachable without hovering. */
export function SeriesTable({
  points,
  unit,
  seriesLabel,
  granularity,
}: {
  points: SeriesPoint[];
  unit: Unit;
  seriesLabel: string;
  granularity: Granularity;
}) {
  return (
    <div className="max-h-80 overflow-auto rounded-lg border border-line">
      <table className="w-full text-sm">
        <caption className="sr-only">{seriesLabel} by {granularity}</caption>
        <thead className="sticky top-0 bg-surface-2 text-left text-xs text-ink-2">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">Period</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">{seriesLabel}</th>
            {unit !== "trips" ? <th scope="col" className="px-3 py-2 text-right font-medium">Trips</th> : null}
          </tr>
        </thead>
        <tbody className="tabular">
          {points.map((point) => (
            <tr key={point.bucket} className="border-t border-line">
              <td className="px-3 py-1.5 text-ink-2">{formatBucket(point.bucket, granularity)}</td>
              <td className="px-3 py-1.5 text-right text-ink">{formatValue(point.value, unit, { exact: true })}</td>
              {unit !== "trips" ? <td className="px-3 py-1.5 text-right text-ink-2">{formatInteger(point.trips)}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
