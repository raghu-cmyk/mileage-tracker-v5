import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Union

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    authenticate_user,
    clear_session,
    create_user,
    establish_session,
    get_current_user_id,
    get_user_count,
    require_authenticated_user,
)
from .categories import list_categories, seed_trip_categories
from .deductions import compute_year_summary, format_cents
from .exports import render_trip_log_csv, render_year_summary_pdf
from .exceptions import RateResolutionError
from .rates import seed_mileage_rates, rate_cents_per_mile_decimal
from .database import Base, SessionLocal, engine, get_db
from .exceptions import MileageTrackerError
from .models import User
from .receipts import (
    delete_receipt,
    get_receipt,
    list_receipts_for_trip,
    read_receipt_bytes,
    store_receipt,
)
from .trips import create_trip, delete_trip, get_trip, list_trips, update_trip
from .vehicles import (
    archive_vehicle,
    create_vehicle,
    delete_vehicle,
    get_vehicle,
    list_odometer_readings,
    list_vehicles,
    update_vehicle,
    upsert_odometer_reading,
    vehicle_has_trips,
)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(title="Mileage Tracker v5", version="5.0.0")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-only-change-in-production"),
    session_cookie="mileage_session",
    max_age=3600,
    same_site="lax",
    https_only=os.environ.get("ENFORCE_HTTPS", "0") == "1",
)
if os.environ.get("ENFORCE_HTTPS", "0") == "1":
    app.add_middleware(HTTPSRedirectMiddleware)

static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_trip_categories(db)
        seed_mileage_rates(db)
    finally:
        db.close()


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _redirect_with_error(url: str, error: str) -> RedirectResponse:
    from urllib.parse import quote

    return RedirectResponse(url=f"{url}?error={quote(error)}", status_code=303)


def _require_user_or_redirect(request: Request, db: Session) -> Union[User, RedirectResponse]:
    if get_current_user_id(request) is None:
        return RedirectResponse(url="/login", status_code=303)
    return require_authenticated_user(request, db)


def _parse_optional_int(raw: Optional[str]) -> Optional[int]:
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _parse_optional_date(raw: Optional[str]) -> Optional[date]:
    if raw is None or raw.strip() == "":
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def _format_rate(rate_cents_per_mile: int) -> str:
    return str(rate_cents_per_mile_decimal(rate_cents_per_mile).normalize())


templates.env.globals["format_cents"] = format_cents
templates.env.globals["format_rate"] = _format_rate


def _available_tax_years() -> list[int]:
    current = datetime.now().year
    return list(range(current, current - 6, -1))


@app.get("/", response_class=HTMLResponse)
def root(request: Request, db: Session = Depends(get_db)):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    vehicles = list_vehicles(db)
    recent_trips = list_trips(db)[:5]
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "title": "Dashboard", "vehicles": vehicles, "recent_trips": recent_trips},
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if get_current_user_id(request) is not None:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "title": "Login",
            "registration_open": get_user_count(db) == 0,
            "error": request.query_params.get("error"),
        },
    )


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, username, password, _client_key(request))
    establish_session(request, user.id)
    return RedirectResponse(url="/", status_code=303)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)):
    if get_current_user_id(request) is not None:
        return RedirectResponse(url="/", status_code=303)
    if get_user_count(db) >= 1:
        return RedirectResponse(url="/login?error=Registration+is+closed", status_code=303)
    return templates.TemplateResponse(
        request,
        "register.html",
        {"title": "Register", "error": request.query_params.get("error")},
    )


@app.post("/register")
def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    if password != confirm_password:
        return RedirectResponse(url="/register?error=Passwords+do+not+match", status_code=303)
    if len(password) < 8:
        return RedirectResponse(
            url="/register?error=Password+must+be+at+least+8+characters",
            status_code=303,
        )
    user = create_user(db, username, password)
    establish_session(request, user.id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    clear_session(request)
    return RedirectResponse(url="/login", status_code=303)


@app.get("/vehicles", response_class=HTMLResponse)
def vehicles_list(request: Request, db: Session = Depends(get_db)):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    show_archived = request.query_params.get("archived") == "1"
    vehicles = list_vehicles(db, include_archived=show_archived)
    return templates.TemplateResponse(
        request,
        "vehicles_list.html",
        {
            "user": user,
            "title": "Vehicles",
            "vehicles": vehicles,
            "show_archived": show_archived,
            "error": request.query_params.get("error"),
            "success": request.query_params.get("success"),
        },
    )


@app.get("/vehicles/new", response_class=HTMLResponse)
def vehicles_new_page(request: Request, db: Session = Depends(get_db)):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(
        request,
        "vehicle_form.html",
        {"user": user, "title": "Add Vehicle", "vehicle": None, "error": request.query_params.get("error")},
    )


@app.post("/vehicles/new")
def vehicles_new_submit(
    request: Request,
    display_name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    try:
        vehicle = create_vehicle(db, display_name=display_name, description=description)
    except MileageTrackerError as exc:
        return _redirect_with_error("/vehicles/new", exc.message)
    return RedirectResponse(url=f"/vehicles/{vehicle.id}?success=Vehicle+created", status_code=303)


@app.get("/vehicles/{vehicle_id}", response_class=HTMLResponse)
def vehicle_detail(vehicle_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    vehicle = get_vehicle(db, vehicle_id)
    if vehicle is None:
        return RedirectResponse(url="/vehicles?error=Vehicle+not+found", status_code=303)
    readings = list_odometer_readings(db, vehicle.id)
    return templates.TemplateResponse(
        request,
        "vehicle_detail.html",
        {
            "user": user,
            "title": vehicle.display_name,
            "vehicle": vehicle,
            "readings": readings,
            "has_trips": vehicle_has_trips(db, vehicle.id),
            "current_tax_year": datetime.now().year,
            "error": request.query_params.get("error"),
            "success": request.query_params.get("success"),
        },
    )


@app.get("/vehicles/{vehicle_id}/edit", response_class=HTMLResponse)
def vehicle_edit_page(vehicle_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    vehicle = get_vehicle(db, vehicle_id)
    if vehicle is None:
        return RedirectResponse(url="/vehicles?error=Vehicle+not+found", status_code=303)
    return templates.TemplateResponse(
        request,
        "vehicle_form.html",
        {
            "user": user,
            "title": f"Edit {vehicle.display_name}",
            "vehicle": vehicle,
            "error": request.query_params.get("error"),
        },
    )


@app.post("/vehicles/{vehicle_id}/edit")
def vehicle_edit_submit(
    vehicle_id: int,
    request: Request,
    display_name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    vehicle = get_vehicle(db, vehicle_id)
    if vehicle is None:
        return RedirectResponse(url="/vehicles?error=Vehicle+not+found", status_code=303)
    try:
        update_vehicle(db, vehicle, display_name=display_name, description=description)
    except MileageTrackerError as exc:
        return _redirect_with_error(f"/vehicles/{vehicle_id}/edit", exc.message)
    return RedirectResponse(url=f"/vehicles/{vehicle_id}?success=Vehicle+updated", status_code=303)


@app.post("/vehicles/{vehicle_id}/archive")
def vehicle_archive_submit(vehicle_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    vehicle = get_vehicle(db, vehicle_id)
    if vehicle is None:
        return RedirectResponse(url="/vehicles?error=Vehicle+not+found", status_code=303)
    archive_vehicle(db, vehicle)
    return RedirectResponse(url="/vehicles?success=Vehicle+archived", status_code=303)


@app.post("/vehicles/{vehicle_id}/delete")
def vehicle_delete_submit(vehicle_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    vehicle = get_vehicle(db, vehicle_id)
    if vehicle is None:
        return RedirectResponse(url="/vehicles?error=Vehicle+not+found", status_code=303)
    try:
        delete_vehicle(db, vehicle)
    except MileageTrackerError as exc:
        return _redirect_with_error(f"/vehicles/{vehicle_id}", exc.message)
    return RedirectResponse(url="/vehicles?success=Vehicle+deleted", status_code=303)


@app.post("/vehicles/{vehicle_id}/odometer")
def vehicle_odometer_submit(
    vehicle_id: int,
    request: Request,
    tax_year: int = Form(...),
    odometer_year_start: str = Form(""),
    odometer_year_end: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    vehicle = get_vehicle(db, vehicle_id)
    if vehicle is None:
        return RedirectResponse(url="/vehicles?error=Vehicle+not+found", status_code=303)
    try:
        upsert_odometer_reading(
            db,
            vehicle,
            tax_year=tax_year,
            odometer_year_start=odometer_year_start,
            odometer_year_end=odometer_year_end,
        )
    except MileageTrackerError as exc:
        return _redirect_with_error(f"/vehicles/{vehicle_id}", exc.message)
    return RedirectResponse(
        url=f"/vehicles/{vehicle_id}?success=Odometer+readings+saved+for+{tax_year}",
        status_code=303,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/summary", response_class=HTMLResponse)
def year_summary_page(request: Request, db: Session = Depends(get_db)):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    tax_year = _parse_optional_int(request.query_params.get("tax_year")) or datetime.now().year
    available_years = _available_tax_years()
    try:
        summary = compute_year_summary(db, tax_year)
    except RateResolutionError as exc:
        return templates.TemplateResponse(
            request,
            "year_summary.html",
            {
                "user": user,
                "title": f"Year-end summary {tax_year}",
                "summary": None,
                "tax_year": tax_year,
                "available_years": available_years,
                "error": exc.message,
            },
        )
    return templates.TemplateResponse(
        request,
        "year_summary.html",
        {
            "user": user,
            "title": f"Year-end summary {tax_year}",
            "summary": summary,
            "tax_year": tax_year,
            "available_years": available_years,
            "error": request.query_params.get("error"),
        },
    )


@app.get("/export/csv/{tax_year}")
def export_trip_log_csv(tax_year: int, request: Request, db: Session = Depends(get_db)):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    try:
        csv_text = render_trip_log_csv(db, tax_year)
    except RateResolutionError as exc:
        return _redirect_with_error(f"/summary?tax_year={tax_year}", exc.message)
    filename = f"mileage-trip-log-{tax_year}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/pdf/{tax_year}")
def export_year_summary_pdf(tax_year: int, request: Request, db: Session = Depends(get_db)):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    try:
        pdf_bytes = render_year_summary_pdf(db, tax_year)
    except RateResolutionError as exc:
        return _redirect_with_error(f"/summary?tax_year={tax_year}", exc.message)
    filename = f"mileage-summary-{tax_year}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/summary/{tax_year}")
def year_summary_api(tax_year: int, db: Session = Depends(get_db)):
    summary = compute_year_summary(db, tax_year)
    return {
        "tax_year": summary.tax_year,
        "total_miles": str(summary.total_miles),
        "deductible_miles": str(summary.deductible_miles),
        "personal_miles": str(summary.personal_miles),
        "total_deduction_cents": summary.total_deduction_cents,
        "business_use_percentage": (
            str(summary.business_use_percentage) if summary.business_use_percentage is not None else None
        ),
        "late_entered_count": summary.late_entered_count,
        "by_category": [
            {
                "category_code": row.category_code,
                "display_name": row.display_name,
                "is_deductible": row.is_deductible,
                "total_miles": str(row.total_miles),
                "total_deduction_cents": row.total_deduction_cents,
            }
            for row in summary.by_category
        ],
    }


@app.get("/trips", response_class=HTMLResponse)
def trips_list(request: Request, db: Session = Depends(get_db)):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    vehicle_id = _parse_optional_int(request.query_params.get("vehicle_id"))
    category_id = _parse_optional_int(request.query_params.get("category_id"))
    date_from = _parse_optional_date(request.query_params.get("date_from"))
    date_to = _parse_optional_date(request.query_params.get("date_to"))
    trips = list_trips(
        db,
        vehicle_id=vehicle_id,
        category_id=category_id,
        date_from=date_from,
        date_to=date_to,
    )
    return templates.TemplateResponse(
        request,
        "trips_list.html",
        {
            "user": user,
            "title": "Trips",
            "trips": trips,
            "vehicles": list_vehicles(db),
            "categories": list_categories(db),
            "filters": {
                "vehicle_id": vehicle_id,
                "category_id": category_id,
                "date_from": request.query_params.get("date_from", ""),
                "date_to": request.query_params.get("date_to", ""),
            },
            "error": request.query_params.get("error"),
            "success": request.query_params.get("success"),
        },
    )


@app.get("/trips/new", response_class=HTMLResponse)
def trips_new_page(request: Request, db: Session = Depends(get_db)):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(
        request,
        "trip_form.html",
        {
            "user": user,
            "title": "Add Trip",
            "trip": None,
            "vehicles": list_vehicles(db),
            "categories": list_categories(db),
            "error": request.query_params.get("error"),
        },
    )


@app.post("/trips/new")
def trips_new_submit(
    request: Request,
    trip_date: str = Form(""),
    origin: str = Form(""),
    destination: str = Form(""),
    business_purpose: str = Form(""),
    miles: str = Form(""),
    category_id: str = Form(""),
    vehicle_id: str = Form(""),
    odometer_start: str = Form(""),
    odometer_end: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    try:
        trip = create_trip(
            db,
            trip_date_raw=trip_date,
            origin=origin,
            destination=destination,
            business_purpose=business_purpose,
            miles_raw=miles,
            category_id_raw=category_id,
            vehicle_id_raw=vehicle_id,
            odometer_start_raw=odometer_start,
            odometer_end_raw=odometer_end,
        )
    except MileageTrackerError as exc:
        return _redirect_with_error("/trips/new", exc.message)
    return RedirectResponse(url=f"/trips/{trip.id}?success=Trip+created", status_code=303)


@app.get("/trips/{trip_id}", response_class=HTMLResponse)
def trip_detail(trip_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    trip = get_trip(db, trip_id)
    if trip is None:
        return RedirectResponse(url="/trips?error=Trip+not+found", status_code=303)
    receipts = list_receipts_for_trip(db, trip_id)
    return templates.TemplateResponse(
        request,
        "trip_detail.html",
        {
            "user": user,
            "title": f"Trip on {trip.trip_date.isoformat()}",
            "trip": trip,
            "receipts": receipts,
            "error": request.query_params.get("error"),
            "success": request.query_params.get("success"),
        },
    )


@app.post("/trips/{trip_id}/receipts")
async def trip_receipt_upload(
    trip_id: int,
    request: Request,
    receipt_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    trip = get_trip(db, trip_id)
    if trip is None:
        return RedirectResponse(url="/trips?error=Trip+not+found", status_code=303)
    try:
        data = await receipt_file.read()
        store_receipt(
            db,
            trip,
            filename=receipt_file.filename or "receipt",
            content_type=receipt_file.content_type,
            data=data,
        )
    except MileageTrackerError as exc:
        return _redirect_with_error(f"/trips/{trip_id}", exc.message)
    return RedirectResponse(url=f"/trips/{trip_id}?success=Receipt+uploaded", status_code=303)


@app.post("/trips/{trip_id}/receipts/{receipt_id}/delete")
def trip_receipt_delete(
    trip_id: int,
    receipt_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    trip = get_trip(db, trip_id)
    if trip is None:
        return RedirectResponse(url="/trips?error=Trip+not+found", status_code=303)
    receipt = get_receipt(db, receipt_id)
    if receipt is None or receipt.trip_id != trip_id:
        return _redirect_with_error(f"/trips/{trip_id}", "Receipt not found.")
    delete_receipt(db, receipt)
    return RedirectResponse(url=f"/trips/{trip_id}?success=Receipt+deleted", status_code=303)


@app.get("/receipts/{receipt_id}/image")
def receipt_image(receipt_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    receipt = get_receipt(db, receipt_id)
    if receipt is None:
        return Response(status_code=404, content="Receipt not found.")
    trip = get_trip(db, receipt.trip_id)
    if trip is None:
        return Response(status_code=404, content="Trip not found.")
    data = read_receipt_bytes(receipt)
    if data is None:
        return Response(status_code=404, content="Receipt image file is missing.")
    return Response(content=data, media_type=receipt.content_type)


@app.get("/trips/{trip_id}/edit", response_class=HTMLResponse)
def trip_edit_page(trip_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    trip = get_trip(db, trip_id)
    if trip is None:
        return RedirectResponse(url="/trips?error=Trip+not+found", status_code=303)
    return templates.TemplateResponse(
        request,
        "trip_form.html",
        {
            "user": user,
            "title": f"Edit Trip on {trip.trip_date.isoformat()}",
            "trip": trip,
            "vehicles": list_vehicles(db),
            "categories": list_categories(db),
            "error": request.query_params.get("error"),
        },
    )


@app.post("/trips/{trip_id}/edit")
def trip_edit_submit(
    trip_id: int,
    request: Request,
    trip_date: str = Form(""),
    origin: str = Form(""),
    destination: str = Form(""),
    business_purpose: str = Form(""),
    miles: str = Form(""),
    category_id: str = Form(""),
    vehicle_id: str = Form(""),
    odometer_start: str = Form(""),
    odometer_end: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    trip = get_trip(db, trip_id)
    if trip is None:
        return RedirectResponse(url="/trips?error=Trip+not+found", status_code=303)
    try:
        update_trip(
            db,
            trip,
            trip_date_raw=trip_date,
            origin=origin,
            destination=destination,
            business_purpose=business_purpose,
            miles_raw=miles,
            category_id_raw=category_id,
            vehicle_id_raw=vehicle_id,
            odometer_start_raw=odometer_start,
            odometer_end_raw=odometer_end,
        )
    except MileageTrackerError as exc:
        return _redirect_with_error(f"/trips/{trip_id}/edit", exc.message)
    return RedirectResponse(url=f"/trips/{trip_id}?success=Trip+updated", status_code=303)


@app.get("/trips/{trip_id}/delete", response_class=HTMLResponse)
def trip_delete_page(trip_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    trip = get_trip(db, trip_id)
    if trip is None:
        return RedirectResponse(url="/trips?error=Trip+not+found", status_code=303)
    return templates.TemplateResponse(
        request,
        "trip_delete_confirm.html",
        {"user": user, "title": "Delete Trip", "trip": trip, "error": request.query_params.get("error")},
    )


@app.post("/trips/{trip_id}/delete")
def trip_delete_submit(
    trip_id: int,
    request: Request,
    confirm: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _require_user_or_redirect(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if confirm != "yes":
        return _redirect_with_error(f"/trips/{trip_id}/delete", "Please confirm deletion.")
    trip = get_trip(db, trip_id)
    if trip is None:
        return RedirectResponse(url="/trips?error=Trip+not+found", status_code=303)
    delete_trip(db, trip)
    return RedirectResponse(url="/trips?success=Trip+deleted", status_code=303)
