"""IRS-compliant CSV and PDF export for mileage substantiation."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.orm import Session

from .deductions import compute_trip_deduction, compute_year_summary, format_cents
from .rates import rate_cents_per_mile_decimal
from .receipts import list_receipts_for_trip, receipt_reference_summary
from .trips import list_trips

CSV_HEADERS = [
    "date",
    "origin",
    "destination",
    "business_purpose",
    "category",
    "miles",
    "vehicle",
    "applied_rate_cents_per_mile",
    "computed_amount_cents",
    "late_entered",
    "receipt_reference",
]


@dataclass(frozen=True)
class TripLogRow:
    date: str
    origin: str
    destination: str
    business_purpose: str
    category: str
    miles: str
    vehicle: str
    applied_rate_cents_per_mile: str
    computed_amount_cents: str
    late_entered: str
    receipt_reference: str


def _year_bounds(tax_year: int) -> tuple[date, date]:
    return date(tax_year, 1, 1), date(tax_year, 12, 31)


def build_trip_log_rows(db: Session, tax_year: int) -> list[TripLogRow]:
    """Build substantiation rows for every trip in the tax year."""
    year_start, year_end = _year_bounds(tax_year)
    trips = list_trips(db, date_from=year_start, date_to=year_end)
    trips_sorted = sorted(trips, key=lambda trip: (trip.trip_date, trip.id))

    rows: list[TripLogRow] = []
    for trip in trips_sorted:
        deduction = compute_trip_deduction(db, trip)
        if deduction.rate_cents_per_mile is None:
            applied_rate = ""
        else:
            applied_rate = str(deduction.rate_cents_per_mile)

        trip_receipts = list_receipts_for_trip(db, trip.id)
        rows.append(
            TripLogRow(
                date=trip.trip_date.isoformat(),
                origin=trip.origin,
                destination=trip.destination,
                business_purpose=trip.business_purpose,
                category=deduction.category_display_name,
                miles=str(trip.miles),
                vehicle=trip.vehicle.display_name,
                applied_rate_cents_per_mile=applied_rate,
                computed_amount_cents=str(deduction.deduction_cents),
                late_entered="yes" if deduction.is_late_entered else "no",
                receipt_reference=receipt_reference_summary(trip_receipts),
            )
        )
    return rows


def render_trip_log_csv(db: Session, tax_year: int) -> str:
    """Render the full trip log CSV with exact stored values."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_HEADERS)
    for row in build_trip_log_rows(db, tax_year):
        writer.writerow(
            [
                row.date,
                row.origin,
                row.destination,
                row.business_purpose,
                row.category,
                row.miles,
                row.vehicle,
                row.applied_rate_cents_per_mile,
                row.computed_amount_cents,
                row.late_entered,
                row.receipt_reference,
            ]
        )
    return buffer.getvalue()


def _format_rate_for_pdf(stored_rate: int) -> str:
    cents = rate_cents_per_mile_decimal(stored_rate)
    return f"{cents.normalize()}¢/mi"


def render_year_summary_pdf(db: Session, tax_year: int) -> bytes:
    """Render a year-summary PDF suitable for accountant handoff."""
    summary = compute_year_summary(db, tax_year)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, title=f"Mileage Summary {tax_year}")
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"Mileage Tracker — Tax Year {tax_year} Summary", styles["Title"]),
        Spacer(1, 12),
        Paragraph(
            "Year-end mileage deduction summary for accountant review. "
            "All figures reflect stored records without rounding or omission.",
            styles["Normal"],
        ),
        Spacer(1, 16),
    ]

    totals_data = [
        ["Total logged miles", str(summary.total_miles)],
        ["Deductible miles", str(summary.deductible_miles)],
        ["Personal miles (excluded)", str(summary.personal_miles)],
        ["Total deductible amount", format_cents(summary.total_deduction_cents)],
        [
            "Business-use percentage",
            f"{summary.business_use_percentage}%" if summary.business_use_percentage is not None else "—",
        ],
        ["Late-entered trips", str(summary.late_entered_count)],
    ]
    totals_table = Table(totals_data, colWidths=[220, 280])
    totals_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.extend([Paragraph("Totals", styles["Heading2"]), totals_table, Spacer(1, 16)])

    category_header = ["Category", "Miles", "Deductible amount"]
    category_rows = [category_header]
    for row in summary.by_category:
        amount = format_cents(row.total_deduction_cents) if row.is_deductible else "—"
        category_rows.append([row.display_name, str(row.total_miles), amount])

    if len(category_rows) == 1:
        category_rows.append(["No trips recorded", "", ""])

    category_table = Table(category_rows, colWidths=[200, 120, 180])
    category_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ]
        )
    )
    story.extend([Paragraph("By category", styles["Heading2"]), category_table, Spacer(1, 16)])

    trip_header = ["Date", "Category", "Miles", "Rate", "Deduction", "Late", "Receipt"]
    trip_rows = [trip_header]
    for row in summary.trip_deductions:
        rate = _format_rate_for_pdf(row.rate_cents_per_mile) if row.rate_cents_per_mile is not None else "—"
        deduction = format_cents(row.deduction_cents) if row.is_deductible else "—"
        trip_receipts = list_receipts_for_trip(db, row.trip_id)
        if trip_receipts:
            receipt_note = receipt_reference_summary(trip_receipts)
        else:
            receipt_note = "none on file"
        trip_rows.append(
            [
                row.trip_date.isoformat(),
                row.category_display_name,
                str(row.miles),
                rate,
                deduction,
                "yes" if row.is_late_entered else "no",
                receipt_note,
            ]
        )

    if len(trip_rows) == 1:
        trip_rows.append(["No trips recorded", "", "", "", "", "", ""])

    trip_table = Table(trip_rows, colWidths=[60, 75, 45, 60, 65, 35, 110])
    trip_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.extend([Paragraph("Per-trip deductions", styles["Heading2"]), trip_table])

    doc.build(story)
    return buffer.getvalue()
