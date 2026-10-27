import unittest

from openpyxl import Workbook

from CompareKPI_United.mpi_cpi_compare import (
    GREEN_FILL,
    WHITE_FILL,
    YELLOW_FILL,
    MetricValue,
    color_target_cell,
    target_highlight,
)


class TargetHighlightTests(unittest.TestCase):
    def test_six_conditions_and_boundaries(self):
        cases = (
            ("1(1/1)", "4(4/1)", 30, ""),
            ("1(1/1)", "5.99(599/100)", 30, ""),
            ("1(1/1)", "6(6/1)", 30, "提升"),
            ("4(4/1)", "1(1/1)", 30, ""),
            ("5.99(599/100)", "1(1/1)", 30, ""),
            ("6(6/1)", "1(1/1)", 30, "降低"),
            ("2(2/1)", "8(8/1)", 30, "提升"),
            ("2(2/1)", "7.99(799/100)", 30, ""),
            ("15(15/1)", "25(25/1)", 30, "提升"),
            ("15(15/1)", "24.99(2499/100)", 30, ""),
            ("29(29/1)", "100(1/0)", 30, "提升"),
            ("29(29/1)", ">0.001(0.001/0)", 30, "提升"),
            ("8(8/1)", "2(2/1)", 30, "降低"),
            ("7.99(799/100)", "2(2/1)", 30, ""),
            ("25(25/1)", "15(15/1)", 30, "降低"),
            ("24.99(2499/100)", "15(15/1)", 30, ""),
            ("100(1/0)", "29(29/1)", 30, "降低"),
            # Passing the release threshold prevents the respective highlight.
            ("30(30/1)", "120(120/1)", 30, ""),
            ("30(30/1)", "100(1/0)", 30, ""),
            ("120(120/1)", "30(30/1)", 30, ""),
            ("100(1/0)", "30(30/1)", 30, ""),
            ("100(1/0)", "100(2/0)", 30, ""),
            ("100(0/0)", "29(29/1)", 30, ""),
            ("29(29/1)", "100(0/0)", 30, ""),
            ("N/A", "29(29/1)", 30, ""),
            ("29(29/1)", "1.182 (n=3)", 30, ""),
            ("2(2/1)", "8(8/1)", None, ""),
            ("0", "0", 30, ""),
            ("2", "8", 30, "提升"),
            ("8", "2", 30, "降低"),
            ("2", ">80", 30, ""),
            # Use the actual ratio, not the rounded prefix.
            ("2.0(201/100)", "8.0(8/1)", 30, ""),
            ("2.0(2/1)", "8.0(799/100)", 30, ""),
        )
        for kind in ("CPI", "MPI"):
            for base, target, threshold, expected in cases:
                # Fractional MPI distances can be below their event count.
                if kind == "CPI" and "0.001" in target:
                    continue
                with self.subTest(kind=kind, base=base, target=target, threshold=threshold):
                    self.assertEqual(
                        target_highlight(MetricValue(raw=base), MetricValue(raw=target),
                                         threshold, kind),
                        expected,
                    )

    def test_fill_colors_and_reset(self):
        wb = Workbook()
        try:
            cell = wb.active["A1"]
            for result, fill in (
                ("提升", GREEN_FILL), ("降低", YELLOW_FILL),
                ("", WHITE_FILL), ("缺失", WHITE_FILL),
            ):
                color_target_cell(cell, result)
                self.assertEqual(cell.fill.fgColor.rgb, fill.fgColor.rgb)
        finally:
            wb.close()


if __name__ == "__main__":
    unittest.main()
