"use client";

import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Columns3, Download, FileSpreadsheet, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { downloadExtract, type RowScope, saveBlob, useDataset, useExplorerFields, useExplorerRows, useZones } from "@/api/hooks";
import type { ExplorerField, ExplorerRow, QualityScope } from "@/api/types";
import { FilterBar } from "@/components/filters/filter-bar";
import { PageHeader } from "@/components/layout/app-shell";
import { DefinitionsButton } from "@/components/layout/definitions-drawer";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog, Popover, Tooltip } from "@/components/ui/overlays";
import { Segmented } from "@/components/ui/segmented";
import { EmptyBlock, ErrorBlock, LoadingBlock } from "@/components/ui/states";
import { Pill } from "@/components/ui/status";
import { useUrlFilters } from "@/hooks/use-url-filters";
import { cn } from "@/lib/cn";
import { PAYMENT_TYPES } from "@/lib/filters";
import { formatInteger, humanize, MISSING } from "@/lib/format";
import { FLAGS } from "@/lib/quality-labels";

const DEFAULT_COLUMNS = [
  "pickup_datetime",
  "pickup_location_id",
  "dropoff_location_id",
  "trip_distance",
  "trip_duration_minutes",
  "passenger_count",
  "payment_type",
  "total_amount",
  "tip_amount",
  "quality_flags",
];
const NUMERIC = new Set(["trip_distance", "trip_duration_minutes", "passenger_count", "fare_amount", "tip_amount", "tolls_amount", "total_amount", "congestion_surcharge", "airport_fee", "cbd_congestion_fee", "average_speed_mph"]);
const MONEY = new Set(["fare_amount", "tip_amount", "tolls_amount", "total_amount", "congestion_surcharge", "airport_fee", "cbd_congestion_fee"]);
const COLUMN_LABELS: Record<string, string> = {
  pickup_datetime: "Pickup",
  dropoff_datetime: "Drop-off",
  pickup_location_id: "From zone",
  dropoff_location_id: "To zone",
  trip_distance: "Miles",
  trip_duration_minutes: "Minutes",
  passenger_count: "Pax",
  payment_type: "Payment",
  vendor_id: "Vendor",
  rate_code_id: "Rate",
  average_speed_mph: "mph",
  quality_flags: "Flags",
};

function FieldPanel({ fields, periods }: { fields: ExplorerField[]; periods: number }) {
  const [query, setQuery] = useState("");
  const visible = fields.filter((f) => !query || f.name.includes(query.toLowerCase()) || f.description.toLowerCase().includes(query.toLowerCase()));
  return (
    <Card className="overflow-hidden">
      <div className="border-b border-line p-4">
        <h2 className="text-[15px] font-semibold text-ink">Fields</h2>
        <p className="mt-0.5 text-xs text-ink-muted">Curated trip table · {fields.length} columns</p>
        <div className="relative mt-3">
          <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-ink-faint" />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search fields" aria-label="Search fields" className="h-8 w-full rounded-lg border border-line-strong bg-surface pl-8 pr-2 text-[13px] outline-none focus:border-accent" />
        </div>
      </div>
      <ul className="scroll-thin max-h-[640px] divide-y divide-line overflow-y-auto" data-testid="field-catalogue">
        {visible.map((field) => (
          <li key={field.name} className="px-4 py-2.5">
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono text-[12px] font-medium text-ink">{field.name}</span>
              <span className="flex items-center gap-1">
                {field.unit ? <Pill>{field.unit}</Pill> : null}
                <Pill tone={field.origin === "derived" ? "accent" : "neutral"}>{field.origin}</Pill>
              </span>
            </div>
            <p className="mt-1 text-xs leading-relaxed text-ink-2">{field.description}</p>
            {field.codes ? <p className="mt-0.5 text-[11px] text-ink-muted">Codes: {field.codes}</p> : null}
            {field.origin === "source" ? (
              <p className={cn("mt-0.5 text-[11px]", field.available_in === periods ? "text-good-ink" : "text-warning-ink")}>
                In {field.available_in}/{periods} monthly files{field.source_columns.length ? ` as ${field.source_columns.join(", ")}` : ""}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function ExploreView() {
  const [filters, setFilters] = useUrlFilters();
  const dataset = useDataset();
  const fields = useExplorerFields();
  const zones = useZones();
  const [scope, setScope] = useState<RowScope>({ sort: "pickup_datetime", order: "desc", quality: "all" });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [columns, setColumns] = useState<string[]>(DEFAULT_COLUMNS);
  const [confirm, setConfirm] = useState<"csv" | "xlsx" | null>(null);
  const [downloading, setDownloading] = useState(false);
  const scopeKey = JSON.stringify([filters, scope, pageSize]);
  const [lastScope, setLastScope] = useState(scopeKey);
  if (scopeKey !== lastScope) {
    // Filters or scope changed: go back to the first page (state reset during render, no effect needed).
    setLastScope(scopeKey);
    setPage(1);
  }
  const rows = useExplorerRows(filters, scope, page, pageSize);
  const zoneNames = useMemo(() => new Map((zones.data ?? []).map((z) => [z.id, z.zone])), [zones.data]);
  const sortable = new Set(fields.data?.sortable ?? []);
  const total = rows.data?.total ?? 0;
  const maxPreview = rows.data?.max_preview_rows ?? 10_000;
  const lastPage = Math.max(1, Math.ceil(Math.min(total, maxPreview) / pageSize));
  const limit = fields.data?.max_export_rows ?? 100_000;

  const updateScope = (patch: Partial<RowScope>) => {
    const next = { ...scope, ...patch };
    if (!next.flag) delete next.flag;
    setScope(next);
  };
  const toggleSort = (column: string) => {
    if (!sortable.has(column)) return;
    updateScope(scope.sort === column ? { order: scope.order === "desc" ? "asc" : "desc" } : { sort: column, order: "desc" });
  };

  const formatCell = (column: string, value: ExplorerRow[string]) => {
    if (value === null || value === undefined) return <span className="text-ink-faint">{MISSING}</span>;
    if (column === "quality_flags") {
      const flags = value as string[];
      return flags.length ? (
        <span className="flex gap-1">
          {flags.map((f) => (
            <Pill key={f} tone="warning">{FLAGS[f]?.label ?? humanize(f)}</Pill>
          ))}
        </span>
      ) : (
        <Pill tone="good">clean</Pill>
      );
    }
    if (column.endsWith("_datetime")) return <span className="whitespace-nowrap tabular">{String(value).replace("T", " ").slice(0, 19)}</span>;
    if (column.endsWith("location_id")) {
      const id = Number(value);
      return (
        <Tooltip content={`Zone ${id}`}>
          <span className="whitespace-nowrap">{zoneNames.get(id) ?? `#${id}`}</span>
        </Tooltip>
      );
    }
    if (column === "payment_type") return PAYMENT_TYPES.find((p) => p.id === Number(value))?.label ?? `Code ${String(value)}`;
    if (MONEY.has(column)) return <span className="tabular">{Number(value).toLocaleString("en-US", { style: "currency", currency: "USD" })}</span>;
    if (NUMERIC.has(column)) return <span className="tabular">{Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 })}</span>;
    return String(value);
  };

  const download = async (format: "csv" | "xlsx") => {
    setDownloading(true);
    try {
      const result = await downloadExtract(filters, scope, columns, format);
      saveBlob(result.blob, result.fileName);
      toast.success(`Downloaded ${formatInteger(result.exportedRows)} rows`, {
        description: result.truncated ? `First ${formatInteger(result.exportedRows)} of ${formatInteger(result.totalRows)} matching rows (export limit).` : "All matching rows.",
      });
    } catch (error) {
      toast.error("Export failed", { description: error instanceof ApiError ? error.message : undefined });
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="mx-auto max-w-[1600px]">
      <PageHeader
        title="Explore data"
        description="Browse the curated trip records behind every chart: preview a bounded page, sort, filter by quality, and download a filtered extract."
        actions={<DefinitionsButton />}
      />
      <div className="sticky top-14 z-20 -mx-4 mb-6 border-b border-line/70 bg-page/85 px-4 py-3 backdrop-blur-md md:-mx-8 md:px-8">
        <FilterBar coverage={dataset.data} filters={filters} onChange={setFilters} />
      </div>
      <div className="grid gap-6 xl:grid-cols-[320px_1fr]">
        <div className="order-2 xl:order-1">
          {fields.data ? <FieldPanel fields={fields.data.fields} periods={fields.data.periods.length} /> : <LoadingBlock />}
        </div>
        <Card className="order-1 min-w-0 overflow-hidden xl:order-2">
          <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
            <Segmented<QualityScope>
              label="Quality"
              value={scope.quality}
              onChange={(quality) => updateScope({ quality, flag: undefined })}
              options={[{ value: "all", label: "All rows" }, { value: "clean", label: "Clean" }, { value: "flagged", label: "Flagged" }]}
            />
            <select
              aria-label="Only rows with flag"
              value={scope.flag ?? ""}
              onChange={(e) => updateScope({ flag: e.target.value || undefined, quality: "all" })}
              className="h-8 rounded-lg border border-line-strong bg-surface px-2 text-[13px] text-ink outline-none focus:border-accent"
            >
              <option value="">Any flag</option>
              {Object.entries(FLAGS).map(([id, f]) => (
                <option key={id} value={id}>
                  {f.label}
                </option>
              ))}
            </select>
            <Popover
              align="start"
              className="w-64"
              trigger={
                <Button variant="secondary" size="sm">
                  <Columns3 className="size-3.5" /> Columns ({columns.length})
                </Button>
              }
            >
              <div className="scroll-thin max-h-72 space-y-0.5 overflow-y-auto">
                {(fields.data?.fields ?? []).map((field) => (
                  <label key={field.name} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-[13px] hover:bg-surface-3">
                    <input
                      type="checkbox"
                      checked={columns.includes(field.name)}
                      disabled={columns.length === 1 && columns.includes(field.name)}
                      onChange={() =>
                        setColumns(columns.includes(field.name) ? columns.filter((c) => c !== field.name) : (fields.data?.fields ?? []).map((f) => f.name).filter((n) => n === field.name || columns.includes(n)))
                      }
                      className="accent-[var(--accent)]"
                    />
                    <span className="font-mono text-xs">{field.name}</span>
                  </label>
                ))}
              </div>
            </Popover>
            <div className="ml-auto flex items-center gap-2">
              <Button variant="secondary" size="sm" onClick={() => setConfirm("xlsx")} disabled={!total || downloading} data-testid="download-xlsx">
                <FileSpreadsheet className="size-3.5" /> Excel
              </Button>
              <Button variant="primary" size="sm" onClick={() => setConfirm("csv")} disabled={!total || downloading} data-testid="download-csv">
                <Download className="size-3.5" /> {downloading ? "Preparing…" : "Download CSV"}
              </Button>
            </div>
          </div>
          {rows.isPending ? (
            <LoadingBlock className="h-96" label="Loading rows…" />
          ) : rows.isError ? (
            <ErrorBlock error={rows.error} className="h-96" onRetry={() => void rows.refetch()} />
          ) : rows.data.rows.length === 0 ? (
            <EmptyBlock title="No rows match" className="h-96">
              Widen the filters or change the quality scope.
            </EmptyBlock>
          ) : (
            <div className={cn("scroll-thin max-h-[640px] overflow-auto transition-opacity", rows.isPlaceholderData && "opacity-50")}>
              <table className="w-full text-[13px]" data-testid="explorer-table">
                <thead className="sticky top-0 z-10 bg-surface-2 text-left text-xs text-ink-muted shadow-[0_1px_0_var(--line)]">
                  <tr>
                    {columns.map((column) => {
                      const active = scope.sort === column;
                      return (
                        <th key={column} scope="col" aria-sort={active ? (scope.order === "desc" ? "descending" : "ascending") : undefined} className={cn("whitespace-nowrap px-3 py-2 font-medium", NUMERIC.has(column) && "text-right")}>
                          {sortable.has(column) ? (
                            <button type="button" onClick={() => toggleSort(column)} className={cn("inline-flex items-center gap-1 hover:text-ink", active && "text-accent-strong")}>
                              {COLUMN_LABELS[column] ?? humanize(column)}
                              {active ? scope.order === "desc" ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" /> : null}
                            </button>
                          ) : (
                            (COLUMN_LABELS[column] ?? humanize(column))
                          )}
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {rows.data.rows.map((row, i) => (
                    <tr key={`${String(row.pickup_datetime)}-${i}`} className="border-t border-line hover:bg-surface-2/60">
                      {columns.map((column) => (
                        <td key={column} className={cn("whitespace-nowrap px-3 py-2 text-ink", NUMERIC.has(column) && "text-right")}>
                          {formatCell(column, row[column] ?? null)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-3 text-xs text-ink-2">
            <span data-testid="row-count">
              {total ? (
                <>
                  Rows <span className="tabular font-medium text-ink">{formatInteger((page - 1) * pageSize + 1)}–{formatInteger(Math.min(page * pageSize, total))}</span> of{" "}
                  <span className="tabular font-medium text-ink">{formatInteger(total)}</span> matching
                  {total > maxPreview ? <span className="text-ink-muted"> · preview limited to the first {formatInteger(maxPreview)}</span> : null}
                </>
              ) : (
                "No matching rows"
              )}
            </span>
            <div className="flex items-center gap-2">
              <select value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))} aria-label="Rows per page" className="h-8 rounded-lg border border-line-strong bg-surface px-2 text-[13px] outline-none">
                {[25, 50, 100].map((n) => (
                  <option key={n} value={n}>
                    {n} / page
                  </option>
                ))}
              </select>
              <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Previous page">
                <ChevronLeft className="size-4" />
              </Button>
              <span className="tabular">
                {page} / {lastPage}
              </span>
              <Button size="sm" variant="secondary" disabled={page >= lastPage} onClick={() => setPage(page + 1)} aria-label="Next page">
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>
        </Card>
      </div>
      <ConfirmDialog
        open={confirm !== null}
        onOpenChange={(open) => !open && setConfirm(null)}
        title={confirm === "xlsx" ? "Download an Excel extract?" : "Download a CSV extract?"}
        description={
          total > limit
            ? `${formatInteger(total)} rows match. The first ${formatInteger(limit)} (in the current sort order) will be exported with the ${columns.length} visible columns. Narrow the filters for a complete extract.`
            : `All ${formatInteger(total)} matching rows will be exported with the ${columns.length} visible columns. Downloads are logged.`
        }
        confirmLabel="Download"
        onConfirm={() => {
          if (confirm) void download(confirm);
        }}
      />
    </div>
  );
}
