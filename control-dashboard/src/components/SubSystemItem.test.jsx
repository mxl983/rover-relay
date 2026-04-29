import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SubsystemItem } from "./SubSystemItem.jsx";

describe("SubsystemItem", () => {
  it("renders label", () => {
    render(<SubsystemItem label="CAM" dotColor="green" />);
    expect(screen.getByText("CAM")).toBeInTheDocument();
  });
});
