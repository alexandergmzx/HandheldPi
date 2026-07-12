"""Renders the state machine to a 320x240 PIL image. High contrast, big glyphs —
this is read at arm's length on a 2-inch panel in a warehouse aisle."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from ..state_machine import PickingStateMachine, State
from . import SCREEN_SIZE

BG = (10, 12, 16)
FG = (235, 235, 235)
DIM = (135, 140, 150)
ACCENT = (255, 200, 40)
OK = (80, 210, 100)
ERR = (215, 45, 45)
BAR = (26, 30, 40)

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
_fonts: dict[int, ImageFont.FreeTypeFont] = {}


def _font(size: int):
    if size not in _fonts:
        for path in _FONT_PATHS:
            try:
                _fonts[size] = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
        else:
            _fonts[size] = ImageFont.load_default()
    return _fonts[size]


def _center(d: ImageDraw.ImageDraw, text: str, y: int, size: int, fill=FG) -> None:
    d.text((SCREEN_SIZE[0] // 2, y), text, font=_font(size), fill=fill, anchor="mm")


_HINTS = {
    State.LOGIN_BADGE: "[X] PIN login",
    State.LOGIN_PIN: "[^v<>] digits   [A] OK   [B] back",
    State.IDLE: "[A] next task   [SEL] status",
    State.NO_TASK: "[A] retry   [B] back",
    State.GOTO_LOCATION: "scan location label",
    State.SCAN_ARTICLE: "scan article   [B] back",
    State.SET_QUANTITY: "[^v] qty   [A] confirm   [B] back",
    State.CONFIRMED: "[A] continue",
}


def render(sm: PickingStateMachine) -> Image.Image:
    img = Image.new("RGB", SCREEN_SIZE, BG)
    d = ImageDraw.Draw(img)
    w, h = SCREEN_SIZE

    # header
    d.rectangle((0, 0, w, 22), fill=BAR)
    who = sm.session.operator_name if sm.session else "-"
    d.text((6, 11), f"{sm.cfg.device.id}  {who}", font=_font(13), fill=FG, anchor="lm")
    right = []
    if sm.queue_depth:
        right.append((f"Q:{sm.queue_depth}", ACCENT))
    right.append(("ON" if sm.online else "OFF", OK if sm.online else ERR))
    x = w - 6
    for text, color in reversed(right):
        d.text((x, 11), text, font=_font(13), fill=color, anchor="rm")
        x -= int(d.textlength(text, font=_font(13))) + 10

    if sm.show_status:
        _body_status(d, sm)
    else:
        _BODY[sm.state](d, sm)

    # footer: hints, overridden by the error banner
    err = sm.error_text
    if err:
        d.rectangle((0, h - 34, w, h), fill=ERR)
        _center(d, err[:34], h - 17, 15, FG)
    else:
        hint = _HINTS.get(sm.state, "")
        if hint:
            d.rectangle((0, h - 22, w, h), fill=BAR)
            _center(d, hint, h - 11, 12, DIM)
    return img


def _body_status(d, sm: PickingStateMachine) -> None:
    _center(d, "STATUS", 42, 16, DIM)
    lines = [
        ("Device", f"{sm.cfg.device.id} @ {sm.cfg.device.site or '-'}"),
        ("Operator", sm.session.operator_id if sm.session else "-"),
        ("WMS", sm.cfg.wms.base_url),
        ("Link", "ONLINE" if sm.online else "OFFLINE"),
        ("Queue", f"{sm.queue_depth} pending"),
        ("Version", sm.version),
    ]
    y = 68
    for key, val in lines:
        d.text((14, y), key, font=_font(13), fill=DIM)
        d.text((100, y), str(val)[:30], font=_font(13), fill=FG)
        y += 22


def _body_startup(d, sm) -> None:
    _center(d, "HHT", 100, 44, ACCENT)
    _center(d, "starting…", 140, 16, DIM)


def _body_login_badge(d, sm) -> None:
    _center(d, "SCAN BADGE", 95, 30)
    _center(d, "operator badge QR", 130, 15, DIM)


def _body_login_pin(d, sm) -> None:
    _center(d, "ENTER PIN", 60, 20)
    n = len(sm.pin_digits)
    box_w, gap = 34, 10
    x0 = (SCREEN_SIZE[0] - n * box_w - (n - 1) * gap) // 2
    for i, digit in enumerate(sm.pin_digits):
        x = x0 + i * (box_w + gap)
        color = ACCENT if i == sm.pin_cursor else DIM
        d.rectangle((x, 95, x + box_w, 145), outline=color, width=2)
        d.text((x + box_w // 2, 120), str(digit), font=_font(28), fill=FG, anchor="mm")


def _body_idle(d, sm) -> None:
    _center(d, "READY", 90, 30, OK)
    name = sm.session.operator_name if sm.session else ""
    _center(d, name, 130, 17, DIM)


def _body_no_task(d, sm) -> None:
    _center(d, "NO TASKS", 100, 28, DIM)
    _center(d, "server queue is empty", 135, 14, DIM)


def _body_goto_location(d, sm) -> None:
    t = sm.task
    _center(d, "GO TO", 45, 15, DIM)
    _center(d, t.location_code, 90, 42, ACCENT)
    _center(d, f"{t.article.description}", 140, 15)
    _center(d, f"qty {t.qty_requested}", 165, 15, DIM)


def _body_scan_article(d, sm) -> None:
    t = sm.task
    _center(d, f"LOC {t.location_code} ✓", 42, 15, OK)
    _center(d, "PICK", 70, 15, DIM)
    _center(d, t.article.sku, 100, 28)
    _center(d, t.article.description, 132, 15, DIM)
    _center(d, f"qty {t.qty_requested}", 165, 18, ACCENT)


def _body_set_quantity(d, sm) -> None:
    t = sm.task
    _center(d, "QUANTITY", 45, 15, DIM)
    _center(d, str(sm.qty), 105, 56, ACCENT)
    _center(d, f"of {t.qty_requested} requested", 150, 15, DIM)
    if sm.qty < t.qty_requested:
        _center(d, "SHORT PICK", 175, 15, ERR)


def _body_confirmed(d, sm) -> None:
    _center(d, "PICK OK ✓", 95, 32, OK)
    if sm.task:
        _center(d, f"{sm.qty} x {sm.task.article.sku}", 135, 17)
    if not sm.online:
        _center(d, "stored offline — will sync", 165, 14, ACCENT)


_BODY = {
    State.STARTUP: _body_startup,
    State.LOGIN_BADGE: _body_login_badge,
    State.LOGIN_PIN: _body_login_pin,
    State.IDLE: _body_idle,
    State.NO_TASK: _body_no_task,
    State.GOTO_LOCATION: _body_goto_location,
    State.SCAN_ARTICLE: _body_scan_article,
    State.SET_QUANTITY: _body_set_quantity,
    State.CONFIRMED: _body_confirmed,
}
