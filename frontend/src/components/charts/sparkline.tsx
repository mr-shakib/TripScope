/** Tiny trend line for stat tiles: de-emphasised line + soft wash, last point marked. Pure SVG. */
export function Sparkline({ values, className = "h-10 w-full" }: { values: (number | null)[]; className?: string }) {
  const points = values.map((v, i) => [i, v] as const).filter((p): p is readonly [number, number] => p[1] !== null);
  if (points.length < 2) return <div className={className} aria-hidden />;
  const width = 120;
  const height = 36;
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const [minX, maxX] = [Math.min(...xs), Math.max(...xs)];
  const [minY, maxY] = [Math.min(...ys), Math.max(...ys)];
  const sx = (x: number) => ((x - minX) / (maxX - minX || 1)) * (width - 4) + 2;
  const sy = (y: number) => height - 3 - ((y - minY) / (maxY - minY || 1)) * (height - 8);
  const path = points.map(([x, y], i) => `${i ? "L" : "M"}${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join(" ");
  const last = points[points.length - 1]!;
  const area = `${path} L${sx(last[0]).toFixed(1)},${height} L${sx(points[0]![0]).toFixed(1)},${height} Z`;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className={className} aria-hidden>
      <path d={area} fill="var(--series-1)" opacity={0.1} />
      <path d={path} fill="none" stroke="var(--series-1)" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      <circle cx={sx(last[0])} cy={sy(last[1])} r={2.5} fill="var(--series-1)" stroke="var(--surface)" strokeWidth={1.5} />
    </svg>
  );
}
