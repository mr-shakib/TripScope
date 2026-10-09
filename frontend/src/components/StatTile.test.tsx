import { fireEvent, render, screen } from "@testing-library/react";

import type { MetricDefinition } from "../api/types";
import { StatTile } from "./StatTile";

const definition: MetricDefinition = {
  id: "avg_trip_distance",
  label: "Average trip distance",
  unit: "miles",
  description: "Mean taximeter distance in miles.",
  rows_included: "Trips with 0 < trip_distance ≤ 200 miles.",
  caveats: ["Rows flagged for this value are excluded."],
};

describe("StatTile", () => {
  it("shows the value, the excluded-row count and the definition on demand", () => {
    render(<StatTile definition={definition} kpi={{ value: 3.179, unit: "miles", excluded_rows: 91015 }} />);
    expect(screen.getByTestId("kpi-avg_trip_distance")).toHaveTextContent("3.18 mi");
    expect(screen.getByText("Excludes 91,015 flagged trips")).toBeInTheDocument();
    expect(screen.getByText(definition.description)).not.toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Definition" }));
    expect(screen.getByText(definition.description)).toBeVisible();
  });

  it("renders unavailable data as a dash, not zero", () => {
    render(<StatTile definition={definition} kpi={{ value: null, unit: "miles", excluded_rows: 0 }} />);
    expect(screen.getByTestId("kpi-avg_trip_distance")).toHaveTextContent("—");
  });
});
