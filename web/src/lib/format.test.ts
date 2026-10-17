import { codeTypeOf, dollars, shortHash } from "./format";

describe("dollars", () => {
  it("formats cents without changing the amount", () => {
    expect(dollars(24336)).toBe("$243.36");
    expect(dollars(152505)).toBe("$1,525.05");
    expect(dollars(10)).toBe("$0.10");
  });

  it("says 'not published' for a missing price, never $0", () => {
    expect(dollars(null)).toBe("not published");
    expect(dollars(undefined)).toBe("not published");
    expect(dollars(0)).toBe("$0.00"); // a real zero stays a real zero
  });
});

describe("codeTypeOf", () => {
  it.each([
    ["73721", "CPT"],
    ["0042T", "CPT"],
    ["j1100", "HCPCS"],
    ["470", "DRG"],
    ["knee mri", null],
    ["7372", null],
  ])("%s -> %s", (input, expected) => {
    expect(codeTypeOf(input)).toBe(expected);
  });
});

it("shortHash keeps both ends of a checksum", () => {
  expect(shortHash("300287d74ffa14e6cf0bc0935ab442732f776e6d9404c6b5bb93b717dd8be335")).toBe("300287d7…8be335");
});
