const express = require("express");
const cookieParser = require("cookie-parser");
const path = require("path");

const PORT = process.env.PORT || 3000;
const BACKEND_URL = (process.env.BACKEND_URL || "http://localhost:5001").replace(/\/+$/, "");
const SECRET_KEY = process.env.SECRET_KEY || "dev-secret-change-me";
const CURRENCY = process.env.CURRENCY_SYMBOL || "$";
const CONNECTOR_TYPES = ["CCS2", "CHAdeMO", "Type 2", "GB/T"];
const EMPTY_STATS = {
  stations: 0, chargers: 0, available: 0, occupied: 0, offline: 0,
  active_sessions: 0, total_energy_kwh: 0, total_revenue: 0,
};

const app = express();
app.disable("x-powered-by");
app.set("view engine", "ejs");
app.set("views", path.join(__dirname, "views"));
app.use(express.static(path.join(__dirname, "public")));
app.use(express.urlencoded({ extended: false })); // reads submitted HTML forms
app.use(cookieParser(SECRET_KEY));

// ---------- helpers ----------
/** Call the Flask backend. Always resolves to { ok, data }. */
async function api(method, apiPath, body) {
  try {
    const res = await fetch(`${BACKEND_URL}${apiPath}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(8000),
    });
    const text = await res.text();
    return { ok: res.ok, data: text ? JSON.parse(text) : {} };
  } catch {
    return { ok: false, data: { error: "Could not reach the backend service" } };
  }
}

// one-time message shown on the next page (signed cookie, like Flask's flash)
function flash(res, text, type = "ok") {
  res.cookie("flash", JSON.stringify({ text, type }), {
    signed: true, httpOnly: true, sameSite: "lax", maxAge: 15000,
  });
}
function notify(res, ok, success, data, fallback = "Something went wrong") {
  flash(res, ok ? success : data.error || fallback, ok ? "ok" : "error");
}

const enc = encodeURIComponent;
const handle = (fn) => (req, res, next) => fn(req, res, next).catch(next);
const clean = (v) => String(v ?? "").trim();

app.use((req, res, next) => {
  res.locals.flash = null;
  const stored = req.signedCookies.flash;
  if (stored) {
    try { res.locals.flash = JSON.parse(stored); } catch { /* ignore bad cookie */ }
    res.clearCookie("flash");
  }
  res.locals.currency = CURRENCY;
  res.locals.money = (n) => Number(n || 0).toFixed(2);
  res.locals.fmtDate = (v) => {
    if (!v) return "-";
    const d = new Date(v);
    if (isNaN(d)) return v;
    return d.toLocaleString("en-GB", {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC",
    }) + " UTC";
  };
  next();
});

// ---------- pages ----------
app.get("/", handle(async (req, res) => {
  const [s, t] = await Promise.all([api("GET", "/api/stations"), api("GET", "/api/stats")]);
  if (!s.ok) res.locals.flash = { text: s.data.error || "Could not load stations", type: "error" };
  res.render("index", {
    title: "Stations",
    stations: s.ok ? s.data : [],
    stats: t.ok ? t.data : EMPTY_STATS,
  });
}));

const blankForm = { name: "", city: "", address: "", price_per_kwh: "0.30" };

app.get("/stations/new", (req, res) =>
  res.render("add_station", { title: "Add station", form: blankForm }));

// Form submit -> Express -> Flask backend
app.post("/stations/new", handle(async (req, res) => {
  const payload = {
    name: clean(req.body.name),
    city: clean(req.body.city),
    address: clean(req.body.address),
    price_per_kwh: clean(req.body.price_per_kwh) || "0",
  };
  const { ok, data } = await api("POST", "/api/stations", payload);
  if (ok) {
    flash(res, "Station added");
    return res.redirect(`/station/${data._id}`);
  }
  res.status(400).render("add_station", {
    title: "Add station",
    form: payload,
    flash: { text: data.error || "Could not add station", type: "error" },
  });
}));

app.get("/station/:id", handle(async (req, res) => {
  const { ok, data } = await api("GET", `/api/stations/${enc(req.params.id)}`);
  if (!ok) {
    flash(res, data.error || "Station not found", "error");
    return res.redirect("/");
  }
  res.render("station", { title: data.name, s: data, types: CONNECTOR_TYPES });
}));

app.post("/station/:id/chargers", handle(async (req, res) => {
  const { ok, data } = await api("POST", `/api/stations/${enc(req.params.id)}/chargers`, {
    connector_type: req.body.connector_type,
    power_kw: req.body.power_kw,
  });
  notify(res, ok, "Charger added", data);
  res.redirect(`/station/${enc(req.params.id)}`);
}));

app.post("/station/:id/chargers/:chargerId/status", handle(async (req, res) => {
  const { ok, data } = await api(
    "PATCH",
    `/api/stations/${enc(req.params.id)}/chargers/${enc(req.params.chargerId)}`,
    { status: req.body.status }
  );
  notify(res, ok, `${req.params.chargerId} is now ${data.status}`, data);
  res.redirect(`/station/${enc(req.params.id)}`);
}));

app.get("/sessions", handle(async (req, res) => {
  const { ok, data } = await api("GET", "/api/sessions");
  if (!ok) res.locals.flash = { text: data.error || "Could not load sessions", type: "error" };
  const list = ok ? data : [];
  res.render("sessions", {
    title: "Sessions",
    active: list.filter((x) => x.status === "active"),
    history: list.filter((x) => x.status === "completed"),
  });
}));

app.post("/sessions/start", handle(async (req, res) => {
  const payload = {
    station_id: clean(req.body.station_id),
    charger_id: clean(req.body.charger_id),
    vehicle_number: clean(req.body.vehicle_number),
  };
  const { ok, data } = await api("POST", "/api/sessions", payload);
  notify(res, ok, `Charging started for ${payload.vehicle_number.toUpperCase()}`, data);
  res.redirect(payload.station_id ? `/station/${enc(payload.station_id)}` : "/");
}));

app.post("/sessions/:id/stop", handle(async (req, res) => {
  const { ok, data } = await api("POST", `/api/sessions/${enc(req.params.id)}/stop`, {
    energy_kwh: req.body.energy_kwh,
  });
  notify(res, ok, `Session finished, total ${data.cost}`, data);
  res.redirect("/sessions");
}));

// ---------- errors ----------
app.use((req, res) =>
  res.status(404).render("error", { title: "Not found", message: "Page not found." }));
app.use((err, req, res, next) => {
  console.error(err);
  res.status(500).render("error", { title: "Error", message: "Something went wrong." });
});

app.listen(PORT, "0.0.0.0", () => console.log(`Frontend running on port ${PORT}, backend at ${BACKEND_URL}`));
