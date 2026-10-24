/*
 * GWN Network Topology card for Home Assistant.
 *
 * Structured view of the GWN network: access points with their clients and bands,
 * switches with their ports, the internet gateway, and uplink wires between them.
 * Served by the gwn_manager integration; data comes from the
 * "gwn_manager/subscribe_topology" WebSocket subscription.
 *
 *   type: custom:gwn-topology-card
 *   title: Mein Netzwerk     # optional, default: GWN Manager entry title
 *   appearance: auto         # auto (follows HA dark mode), dark or light
 *   show_all_ports: false    # also show switch ports without link
 *   uplinks:                 # which port each device is plugged into (the API doesn't tell)
 *     Living Room AP: Switch:17   # "<device>:<port>", or just the port if there is one switch
 *     Office AP: 21
 *     Switch:1: Gateway:LAN1      # "<device>:<own port>" also draws the wire from that port
 *   entry_id: ...            # optional, only one GWN Manager entry
 */

const TOPOLOGY_VERSION = "0.5.5";
const RESUBSCRIBE_MS = 90_000;
const WIRE_COLORS = ["#38c6e8", "#a78bfa", "#3ee08f", "#f5c542", "#f472b6", "#60a5fa"];

const TEXT = {
  en: {
    topology: "Network topology", ap: "Access point", gateway: "Internet gateway", switch: "Switch",
    aps: "APs", switches: "Switches", clients: "Clients", online: "Online", offline: "Offline",
    ap1: "AP", switch1: "Switch", client1: "Client",
    unknown: "Unknown", internetOnline: "Internet online", internetOffline: "Internet offline",
    activePorts: "active ports", uplinkUnknown: "Uplink unknown", noData: "Waiting for data…",
    port: "Port", wired: "Wired", tapHint: "Tap a device to show its connections",
    noClients: "No clients connected",
  },
  de: {
    topology: "Netzwerk-Topologie", ap: "Access Point", gateway: "Internet-Gateway", switch: "Switch",
    aps: "APs", switches: "Switches", clients: "Clients", online: "Online", offline: "Offline",
    ap1: "AP", switch1: "Switch", client1: "Client",
    unknown: "Unbekannt", internetOnline: "Internet online", internetOffline: "Internet offline",
    activePorts: "aktive Ports", uplinkUnknown: "Uplink unbekannt", noData: "Warte auf Daten …",
    port: "Port", wired: "Kabel", tapHint: "Gerät antippen, um Verbindungen zu zeigen",
    noClients: "Keine Clients verbunden",
  },
};

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function formatRate(bps) {
  if (bps === null || bps === undefined) return "–";
  if (bps >= 1e9) return `${(bps / 1e9).toFixed(2)} Gbit/s`;
  if (bps >= 1e6) return `${(bps / 1e6).toFixed(1)} Mbit/s`;
  return `${Math.round(bps / 1e3)} kbit/s`;
}

function formatDuration(seconds) {
  if (!seconds) return "";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  return d ? `${d}d ${h}h` : `${h}h ${Math.floor((seconds % 3600) / 60)}m`;
}

class GwnTopologyCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._networks = null;
    this._lastMessage = 0;
  }

  static getStubConfig() {
    return { uplinks: {} };
  }

  setConfig(config) {
    this._config = { appearance: "auto", show_all_ports: false, uplinks: {}, ...config };
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    this._applyAppearance();
    if (first && this.isConnected) this._subscribe();
  }

  getCardSize() {
    return 10;
  }

  connectedCallback() {
    if (this._hass && !this._unsub) this._subscribe();
    this._resizeObserver = new ResizeObserver(() => this._drawWires());
    this._resizeObserver.observe(this);
    this._watchdog = setInterval(() => {
      if (this._hass && Date.now() - this._lastMessage > RESUBSCRIBE_MS) this._subscribe();
    }, 30_000);
  }

  disconnectedCallback() {
    this._unsubscribe();
    this._resizeObserver?.disconnect();
    clearInterval(this._watchdog);
  }

  _t(key) {
    const lang = (this._hass?.language || "en").split("-")[0];
    return (TEXT[lang] || TEXT.en)[key];
  }

  // ---------------------------------------------------------------- data

  async _subscribe() {
    this._unsubscribe();
    this._lastMessage = Date.now();
    const msg = { type: "gwn_manager/subscribe_topology" };
    if (this._config.entry_id) msg.entry_id = this._config.entry_id;
    try {
      this._unsub = await this._hass.connection.subscribeMessage((data) => {
        this._lastMessage = Date.now();
        this._networks = data.networks;
        this._render();
      }, msg);
    } catch (err) {
      this._error = err.message || String(err);
      this._render();
    }
  }

  _unsubscribe() {
    if (this._unsub) {
      this._unsub().catch?.(() => {});
      this._unsub = null;
    }
  }

  _applyAppearance() {
    const wrap = this.shadowRoot?.querySelector(".wrap");
    if (!wrap) return;
    const mode = this._config.appearance;
    const dark = mode === "dark" || (mode !== "light" && Boolean(this._hass?.themes?.darkMode));
    wrap.classList.toggle("dark", dark);
    wrap.classList.toggle("light", !dark);
  }

  /**
   * Resolve the configured uplinks to { childMac: { parent, port, ownPort } }.
   * Keys: "<device>" or "<device>:<own port>"; values: "<parent>:<port>" or "<port>"
   * (the only switch). Devices may be given by name or MAC.
   */
  _uplinks(devices) {
    const byName = new Map();
    for (const d of devices) {
      byName.set(d.name.toLowerCase(), d);
      byName.set(d.mac.toLowerCase(), d);
    }
    const switches = devices.filter((d) => d.type === "SWITCH");
    const resolve = (text) => {
      // "<device>:<port>", where the device itself may contain ":" (a MAC).
      const value = String(text).trim();
      if (byName.has(value.toLowerCase())) return { device: byName.get(value.toLowerCase()), port: null };
      const split = value.lastIndexOf(":");
      if (split > 0) {
        const device = byName.get(value.slice(0, split).trim().toLowerCase());
        if (device) return { device, port: value.slice(split + 1).trim() };
      }
      return { device: null, port: value };
    };
    const result = {};
    // Uplinks detected by the integration (or set in its options) ...
    for (const d of devices) {
      if (d.uplink?.parent && d.uplink.port) {
        result[d.mac] = { parent: d.uplink.parent, port: String(d.uplink.port), ownPort: d.uplink.ownPort ?? null };
      }
    }
    // ... overridden by the card configuration.
    for (const [key, target] of Object.entries(this._config.uplinks || {})) {
      const child = resolve(key);
      if (!child.device) continue;
      const parent = resolve(target);
      const parentDevice = parent.device || switches[0];
      if (!parentDevice || !parent.port) continue;
      result[child.device.mac] = { parent: parentDevice.mac, port: parent.port, ownPort: child.port };
    }
    return result;
  }

  // ------------------------------------------------------------ rendering

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const networks = this._networks;
    const net = networks?.[0];
    const devices = net?.aps || [];
    const clients = net?.clients || [];
    const uplinks = this._uplinks(devices);
    this._uplinkMap = uplinks;

    // Narrow card (phone): wires only for the selected device, routed along the side.
    this._compact = this._isCompact();
    const switches = devices.filter((d) => d.type === "SWITCH");
    const routers = devices.filter((d) => d.type === "ROUTER" || d.wan);
    // Sorted by the port they are plugged into, so the wires don't cross.
    const portKey = (d) => {
      const port = uplinks[d.mac]?.port;
      const n = parseInt(port, 10);
      return Number.isNaN(n) ? Number.MAX_SAFE_INTEGER : n;
    };
    const aps = devices
      .filter((d) => !switches.includes(d) && !routers.includes(d))
      .sort((a, b) => portKey(a) - portKey(b) || a.name.localeCompare(b.name));
    const connected = clients.filter((c) => c.connected).length;
    const wan = routers.find((r) => r.wan)?.wan;
    const wireColor = new Map(Object.keys(uplinks).map((mac, i) => [mac, WIRE_COLORS[i % WIRE_COLORS.length]]));

    const plural = (n, key, one) => `${n} ${this._t(n === 1 ? one : key)}`;
    const chip = (text, cls = "") => `<span class="chip ${cls}"><b>◈</b>${esc(text)}</span>`;
    const chips = net
      ? [
          chip(plural(aps.length, "aps", "ap1")),
          chip(plural(switches.length, "switches", "switch1")),
          chip(plural(connected, "clients", "client1"), "boxed"),
          wan ? chip(this._t(wan.connected ? "internetOnline" : "internetOffline"), wan.connected ? "ok" : "bad") : "",
        ].join("")
      : "";
    const speedText = (speed) => (speed ? (speed >= 1000 ? `${speed / 1000} Gbit/s` : `${speed} Mbit/s`) : "");
    const bandText = (bands) =>
      Object.entries(bands || {})
        .sort()
        .map(([band, n]) => `<span>${esc(band.replace("G", " GHz"))}: <b class="b${esc(band.replace(/\W/g, ""))}">${n}</b></span>`)
        .join("");
    const deviceName = (mac) => devices.find((d) => d.mac === mac)?.name ?? "";

    const rssiColor = (rssi) =>
      rssi == null ? "var(--muted)" : rssi >= -60 ? "#3ee08f" : rssi >= -70 ? "#a3e635" : rssi >= -80 ? "#f5c542" : "#f87171";
    const clientList = (device) => {
      if (this._selected !== device.mac) return "";
      const list = clients
        .filter((c) => c.connected && c.ap === device.mac)
        .sort((a, b) => Number(a.wired) - Number(b.wired) || (b.rssi ?? -999) - (a.rssi ?? -999));
      if (!list.length) return `<div class="client-list empty-list">${esc(this._t("noClients"))}</div>`;
      return `<div class="client-list">${list
        .map((c) => `
          <div class="client-row" data-client-entity="${esc(c.entity_id ?? "")}">
            <i style="background:${c.wired ? "var(--accent)" : rssiColor(c.rssi)}"></i>
            <span class="cname">${esc(c.name)}</span>
            <span class="cmeta">${c.wired
              ? `${esc(this._t("wired"))}${c.port ? ` · ${esc(c.port)}` : ""}`
              : `${esc((c.band || "").replace("G", " GHz"))}${c.rssi != null ? ` · ${esc(c.rssi)} dBm` : ""}`}</span>
          </div>`)
        .join("")}</div>`;
    };
    const infoButton = (device) =>
      device.entity_id ? `<button class="info" data-info="${esc(device.entity_id)}" title="Info">ⓘ</button>` : "";

    const apCard = (ap) => {
      const total = Object.values(ap.bands || {}).reduce((a, b) => a + b, 0);
      const up = uplinks[ap.mac];
      return `
        <div class="device ap ${ap.online ? "" : "is-offline"} ${this._selected === ap.mac ? "selected" : ""}" data-mac="${esc(ap.mac)}" data-select="${esc(ap.mac)}"
             style="--wire:${wireColor.get(ap.mac) || "var(--muted)"}">
          <div class="device-head">
            <span class="kind"><b>◈</b>${esc(this._t("ap"))}</span>
            <span class="head-right">
              <span class="status ${ap.online ? "ok" : "bad"}"><i></i>${esc(this._t(ap.online ? "online" : "offline"))}</span>
              ${infoButton(ap)}
            </span>
          </div>
          <div class="avatar"><span>G</span><i class="${ap.online ? "ok" : "bad"}"></i></div>
          <div class="name">${esc(ap.name)}</div>
          <div class="model">${esc(ap.model ?? "")}${ap.area ? ` · ${esc(ap.area)}` : ""}</div>
          <div class="clients"><b class="dia">◈</b><b class="n">${total}</b> ${esc(this._t("clients"))}</div>
          <div class="bands">${bandText(ap.bands)}</div>
          <div class="uplink"><b class="dia">◈</b>${up
            ? `${esc(deviceName(up.parent))} · ${esc(this._t("port"))} ${esc(up.port)}`
            : esc(this._t("uplinkUnknown"))}</div>
          ${clientList(ap)}
        </div>`;
    };

    const portBar = (device, port) => {
      const child = Object.entries(uplinks).find(([, u]) => u.parent === device.mac && u.port === port.label);
      const own = uplinks[device.mac]?.ownPort === port.label ? uplinks[device.mac] : null;
      const label = child
        ? deviceName(child[0])
        : own
          ? deviceName(own.parent)
          : port.clients
            ? plural(port.clients, "clients", "client1")
            : "";
      const wire = child ? wireColor.get(child[0]) : own ? wireColor.get(device.mac) : "";
      return `
        <div class="port ${port.up ? "up" : "down"} ${child || own ? "linked" : ""}"
             data-device="${esc(device.mac)}" data-port="${esc(port.label)}" style="${wire ? `--wire:${wire}` : ""}">
          <div class="bar">
            <i class="led"></i>
            ${port.poe ? `<b class="poe">◆</b>` : ""}
            <span class="num">${esc(port.label)}</span>
          </div>
          <div class="port-label">${esc(label)}</div>
          <div class="port-speed">${esc(speedText(port.speed))}</div>
        </div>`;
    };

    const panel = (device, kind) => {
      const ports = (device.ports || []).filter((p) => this._config.show_all_ports || p.up || kind === "gateway");
      const active = (device.ports || []).filter((p) => p.up).length;
      const wireless = Object.entries(device.bands || {});
      const total = wireless.reduce((a, [, n]) => a + n, 0);
      const info = kind === "gateway"
        ? `<div class="wan">
             ${device.wan ? `<div class="rates"><b class="dia">◈</b><span>↓ ${esc(formatRate(device.wan.downloadRate))}</span><span>↑ ${esc(formatRate(device.wan.uploadRate))}</span></div>` : ""}
             ${total ? `<div class="muted small gw-clients"><b class="dia">◈</b>${total} ${esc(this._t("clients"))} · ${bandText(device.bands)}</div>` : ""}
           </div>`
        : `<div class="brand"><span>G</span><small>NETWORK</small></div>`;
      const headRight = kind === "gateway" && device.wan
        ? `<span class="muted small">${esc(device.wan.wanIp ?? "")}${device.wan.connectDuration ? ` · ${esc(formatDuration(device.wan.connectDuration))}` : ""}</span>`
        : kind === "switch"
          ? `<span class="muted small">${active}/${(device.ports || []).length} ${esc(this._t("activePorts"))}</span>`
          : "";
      return `
        <div class="panel ${kind} ${device.online ? "" : "is-offline"} ${this._selected === device.mac ? "selected" : ""}" data-mac="${esc(device.mac)}" data-select="${esc(device.mac)}"
             style="--wire:${wireColor.get(device.mac) || "var(--muted)"}">
          <div class="panel-head">
            <div class="icon-box"><b>◈</b></div>
            <div>
              <div class="name">${esc(device.name)}</div>
              <div class="model">${esc(kind === "gateway" ? this._t("gateway") + " · " : "")}${esc(device.model ?? "")}${device.area ? ` · ${esc(device.area)}` : ""}</div>
            </div>
            <div class="spacer"></div>
            <span class="status ${device.online ? "ok" : "bad"}"><i></i>${esc(this._t(device.online ? "online" : "offline"))}</span>
            ${headRight}
            ${infoButton(device)}
          </div>
          <div class="panel-body ${kind === "switch" ? "faceplate" : "gateway-plate"}">
            ${info}
            <div class="ports">${ports.map((p) => portBar(device, p)).join("")}</div>
          </div>
          ${clientList(device)}
        </div>`;
    };

    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="wrap ${this._compact ? "compact" : ""}">
          <div class="header">
            <div>
              <div class="eyebrow">${esc(this._t("topology"))}</div>
              <div class="title">${esc(this._config.title ?? net?.title ?? "GWN")}</div>
            </div>
            <div class="chips">${chips}</div>
          </div>
          ${this._error ? `<div class="empty bad">${esc(this._error)}</div>` : ""}
          ${!net && !this._error ? `<div class="empty">${esc(this._t("noData"))}</div>` : ""}
          <div class="stage">
            <svg class="wires"></svg>
            ${aps.length ? `<div class="aps">${aps.map(apCard).join("")}</div>` : ""}
            ${switches.map((sw) => panel(sw, "switch")).join("")}
            ${routers.map((r) => panel(r, "gateway")).join("")}
          </div>
          <div class="footer">
            <span class="legend"><i class="ok"></i>${esc(this._t("online"))}</span>
            <span class="legend"><i class="bad"></i>${esc(this._t("offline"))}</span>
            <span class="legend"><i class="unknown"></i>${esc(this._t("unknown"))}</span>
            <span class="spacer"></span>
            <span>${this._compact && !this._selected ? esc(this._t("tapHint")) : "GWN Network Topology"}</span>
          </div>
        </div>
      </ha-card>`;
    this._applyAppearance();
    const moreInfo = (entityId) =>
      entityId && this.dispatchEvent(new CustomEvent("hass-more-info", { bubbles: true, composed: true, detail: { entityId } }));
    for (const el of this.shadowRoot.querySelectorAll("[data-select]")) {
      el.addEventListener("click", () => {
        this._selected = this._selected === el.dataset.select ? null : el.dataset.select;
        this._render();
      });
    }
    for (const el of this.shadowRoot.querySelectorAll("[data-info]")) {
      el.addEventListener("click", (event) => {
        event.stopPropagation();
        moreInfo(el.dataset.info);
      });
    }
    for (const el of this.shadowRoot.querySelectorAll("[data-client-entity]")) {
      el.addEventListener("click", (event) => {
        event.stopPropagation();
        moreInfo(el.dataset.clientEntity);
      });
    }
    requestAnimationFrame(() => this._drawWires());
  }

  _isCompact() {
    const width = this.getBoundingClientRect().width;
    return width > 0 && width < 640;
  }

  _drawWires() {
    const svg = this.shadowRoot?.querySelector(".wires");
    const stage = this.shadowRoot?.querySelector(".stage");
    if (!svg || !stage || !this._uplinkMap) return;
    const compact = this._isCompact();
    if (compact !== this._compact) {
      // Crossed the phone breakpoint (rotation, resize): re-render the layout.
      this._render();
      return;
    }
    const base = stage.getBoundingClientRect();
    svg.setAttribute("width", base.width);
    svg.setAttribute("height", base.height);
    const portBar = (mac, label) =>
      stage.querySelector(`.port[data-device="${CSS.escape(mac)}"][data-port="${CSS.escape(label)}"] .bar`);
    const selected = this._selected;
    const paths = [];
    let lane = 0;
    for (const [index, [childMac, up]] of Object.entries(this._uplinkMap).entries()) {
      const related = selected && (childMac === selected || up.parent === selected);
      if (compact && !related) continue;
      const childEl = stage.querySelector(`[data-mac="${CSS.escape(childMac)}"]`);
      const fromEl = (up.ownPort && portBar(childMac, up.ownPort)) || childEl;
      const toEl = portBar(up.parent, up.port);
      if (!childEl || !fromEl || !toEl) continue;
      const from = fromEl.getBoundingClientRect();
      const to = toEl.getBoundingClientRect();
      const color = getComputedStyle(childEl).getPropertyValue("--wire").trim() || "#22d3ee";
      let d, x1, y1, x2, y2;
      if (compact) {
        // Along the left edge: out of the device's left side, down the gutter,
        // into the port from the left.
        const gutter = 6 + (lane++ % 3) * 5;
        x1 = from.left - base.left;
        y1 = from.top - base.top + Math.min(from.height / 2, 40);
        x2 = to.left - base.left;
        y2 = to.top - base.top + to.height / 2;
        d = `M${x1},${y1} H${gutter} V${y2} H${x2}`;
      } else {
        x1 = from.left + from.width / 2 - base.left;
        x2 = to.left + to.width / 2 - base.left;
        const down = from.top < to.top;
        y1 = (down ? from.bottom : from.top) - base.top;
        y2 = (down ? to.top : to.bottom) - base.top;
        const box = childEl.getBoundingClientRect();
        const edge = (down ? box.bottom : box.top) - base.top;
        const mid = edge + (down ? 1 : -1) * (14 + (index % 4) * 6);
        d = `M${x1},${y1} V${mid} H${x2} V${y2}`;
      }
      const dim = selected && !related ? " dim" : "";
      paths.push(`
        <g class="wire-group${dim}">
          <path d="${d}" class="wire-glow" stroke="${color}"/>
          <path d="${d}" class="wire" stroke="${color}"/>
          <circle cx="${x1}" cy="${y1}" r="3.5" fill="${color}" class="dot"/>
          <circle cx="${x2}" cy="${y2}" r="4" class="ring" stroke="${color}"/>
        </g>`);
    }
    svg.innerHTML = paths.join("");
  }
}

const STYLE = `
  :host { display: block; }
  ha-card { overflow: hidden; border: none; }
  .wrap {
    position: relative; color: var(--text); background: var(--bg);
    font-family: Inter, var(--paper-font-body1_-_font-family, Roboto, sans-serif);
  }
  .wrap.dark {
    --bg: linear-gradient(180deg, #0f1f2b 0%, #0c1a24 100%);
    --text: #f1f5f9; --muted: #8aa0b4; --line: rgba(120, 170, 200, .16);
    --card: linear-gradient(180deg, #13283a 0%, #102231 100%); --card-border: rgba(120, 170, 200, .22);
    --accent: #38c6e8; --ok: #3ee08f; --bad: #f87171; --unknown: #fbbf24;
    --plate: linear-gradient(180deg, #f5f7f9 0%, #dfe5ea 100%); --plate-text: #24323f;
    --gw-plate: linear-gradient(180deg, #123444 0%, #0f2a36 100%);
    --bar: #0c1d24; --glow: 1;
  }
  .wrap.light {
    --bg: linear-gradient(180deg, #f6f8fa 0%, #eef2f5 100%);
    --text: #14202b; --muted: #5d6f80; --line: rgba(20, 32, 43, .1);
    --card: #ffffff; --card-border: rgba(20, 32, 43, .12);
    --accent: #0891b2; --ok: #10b981; --bad: #dc2626; --unknown: #d97706;
    --plate: linear-gradient(180deg, #ffffff 0%, #e6ebf0 100%); --plate-text: #24323f;
    --gw-plate: linear-gradient(180deg, #e8f2f6 0%, #dbe9ef 100%);
    --bar: #102530; --glow: 0;
  }
  .header {
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    padding: 18px 22px 14px; border-bottom: 1px solid var(--line);
  }
  .eyebrow { font-size: 10px; letter-spacing: 2.2px; text-transform: uppercase; color: var(--accent); font-weight: 700; }
  .title { font-size: 20px; font-weight: 700; margin-top: 2px; }
  .chips { margin-left: auto; display: flex; gap: 8px; flex-wrap: wrap; }
  .chip {
    display: inline-flex; align-items: center; gap: 7px; font-size: 12px; padding: 6px 12px;
    border: 1px solid var(--card-border); border-radius: 999px; color: var(--text);
  }
  .chip > b, .dia, .kind > b, .icon-box b { color: var(--accent); font-weight: 400; }
  .chip.boxed { box-shadow: inset 0 0 0 3px transparent; }
  .chip.ok { color: var(--ok); } .chip.ok > b { color: var(--ok); }
  .chip.bad { color: var(--bad); } .chip.bad > b { color: var(--bad); }
  .stage { position: relative; display: flex; flex-direction: column; gap: 40px; padding: 16px 16px 8px; }
  .wires { position: absolute; inset: 0; pointer-events: none; overflow: visible; z-index: 3; }
  .wire { fill: none; stroke-width: 2; }
  .wire-glow { fill: none; stroke-width: 7; opacity: calc(.22 * var(--glow)); }
  .ring { fill: var(--bar); stroke-width: 2; }
  .aps { display: flex; justify-content: center; flex-wrap: wrap; gap: 12px; position: relative; z-index: 1; }
  .device {
    width: 245px; padding: 12px 14px 12px; text-align: center; position: relative; z-index: 1;
    background: var(--card); border: 1px solid var(--card-border); border-radius: 14px; cursor: pointer;
  }
  .device:hover, .panel:hover { border-color: var(--wire); }
  .device-head { display: flex; justify-content: space-between; align-items: center; }
  .kind { font-size: 11px; color: var(--muted); display: inline-flex; gap: 6px; }
  .status {
    display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 600;
    padding: 3px 9px; border-radius: 999px; background: rgba(62, 224, 143, .1); color: var(--text);
  }
  .status i { width: 7px; height: 7px; border-radius: 50%; background: var(--ok); box-shadow: 0 0 6px var(--ok); }
  .status.bad { background: rgba(248, 113, 113, .12); } .status.bad i { background: var(--bad); box-shadow: none; }
  .avatar {
    position: relative; width: 62px; height: 62px; margin: 10px auto 10px; border-radius: 50%;
    display: grid; place-items: center;
    background: radial-gradient(circle at 40% 35%, #ffffff 0%, #e9eef2 70%, #cfd8df 100%);
    box-shadow: 0 0 0 3px rgba(255,255,255,.1), 0 0 calc(22px * var(--glow)) rgba(56, 198, 232, .25);
  }
  .avatar span { font-size: 26px; font-weight: 300; color: #2b3a47; }
  .avatar i {
    position: absolute; right: 8px; bottom: 9px; width: 7px; height: 7px; border-radius: 50%;
    background: var(--ok); box-shadow: 0 0 0 2px #fff;
  }
  .avatar i.bad { background: var(--bad); }
  .name { font-weight: 700; font-size: 14px; }
  .model { font-size: 11px; color: var(--muted); margin-top: 1px; }
  .clients { font-size: 12px; margin-top: 8px; color: var(--muted); display: inline-flex; gap: 6px; align-items: baseline; }
  .clients .n { color: var(--text); font-size: 14px; }
  .bands { font-size: 11px; color: var(--muted); margin-top: 3px; display: flex; justify-content: center; gap: 10px; min-height: 14px; }
  .bands b { color: var(--text); }
  .bands b.b5G, .bands b.b6G { color: var(--ok); }
  .uplink { font-size: 11px; color: var(--muted); margin-top: 18px; display: inline-flex; gap: 6px; }
  .panel {
    position: relative; z-index: 1; border-radius: 14px; overflow: hidden; cursor: pointer;
    background: var(--card); border: 1px solid var(--card-border);
  }
  .panel-head { display: flex; align-items: center; gap: 10px; padding: 10px 12px; }
  .icon-box {
    width: 28px; height: 28px; border-radius: 8px; display: grid; place-items: center;
    border: 1px solid var(--card-border); background: rgba(56, 198, 232, .06); font-size: 13px;
  }
  .spacer { flex: 1; }
  .muted { color: var(--muted); }
  .small { font-size: 11px; }
  .panel-body { display: flex; gap: 16px; align-items: center; padding: 14px 16px 12px; }
  .faceplate { background: var(--plate); color: var(--plate-text); }
  .faceplate .port-label { color: var(--plate-text); }
  .faceplate .port-speed { color: #6b7c8c; }
  .gateway-plate { background: var(--gw-plate); }
  .brand { width: 54px; text-align: center; line-height: 1; }
  .brand span { font-size: 26px; font-weight: 300; }
  .brand small { display: block; font-size: 7px; letter-spacing: 1.2px; font-weight: 700; margin-top: 3px; }
  .wan { min-width: 150px; display: flex; flex-direction: column; gap: 6px; font-size: 12px; }
  .rates { display: flex; gap: 12px; align-items: center; font-weight: 600; }
  .gw-clients { display: flex; gap: 6px; align-items: baseline; flex-wrap: wrap; }
  .ports { display: flex; flex-wrap: wrap; gap: 10px; flex: 1; }
  .port { flex: 1 1 90px; min-width: 70px; text-align: center; }
  .bar {
    position: relative; height: 30px; border-radius: 6px; background: var(--bar);
    border: 2px solid rgba(255, 255, 255, .12); display: flex; align-items: center; justify-content: center;
  }
  .port.up .bar { border-color: var(--ok); box-shadow: 0 0 calc(10px * var(--glow)) rgba(62, 224, 143, .35); }
  .port.down .bar { opacity: .5; }
  .bar .led { position: absolute; left: 6px; top: 5px; width: 5px; height: 5px; border-radius: 50%; background: #3b4b57; }
  .port.up .bar .led { background: var(--ok); box-shadow: 0 0 5px var(--ok); }
  .bar .poe { position: absolute; right: 6px; top: 3px; font-size: 9px; color: #f5c542; }
  .bar .num { color: #e8eef3; font-size: 11px; font-weight: 700; margin-top: 8px; }
  .port-label { font-size: 11px; margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .port-speed { font-size: 9px; color: var(--muted); min-height: 11px; }
  .is-offline { opacity: .6; }
  .wire-group { transition: opacity .2s; }
  .wire-group.dim { opacity: .15; }
  .device.selected, .panel.selected { border-color: var(--wire); box-shadow: 0 0 0 1px var(--wire), 0 0 calc(18px * var(--glow)) color-mix(in srgb, var(--wire) 35%, transparent); }
  .head-right { display: inline-flex; align-items: center; gap: 6px; }
  .info {
    border: 1px solid var(--card-border); background: transparent; color: var(--muted);
    border-radius: 50%; width: 24px; height: 24px; font-size: 13px; line-height: 1; cursor: pointer; padding: 0;
  }
  .info:hover { color: var(--text); border-color: var(--accent); }
  .client-list {
    margin-top: 12px; padding-top: 8px; border-top: 1px solid var(--line); text-align: left;
    display: flex; flex-direction: column; gap: 2px; max-height: 260px; overflow: auto;
  }
  .panel .client-list { margin: 0; padding: 8px 14px 10px; }
  .client-row {
    display: flex; align-items: center; gap: 8px; font-size: 12px; padding: 4px 6px; border-radius: 6px; cursor: pointer;
  }
  .client-row:hover { background: rgba(56, 198, 232, .08); }
  .client-row i { width: 7px; height: 7px; border-radius: 50%; flex: none; }
  .client-row .cname { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .client-row .cmeta { color: var(--muted); font-size: 11px; white-space: nowrap; }
  .empty-list { color: var(--muted); font-size: 12px; text-align: center; }
  .wrap.compact .stage { padding-left: 24px; gap: 22px; }
  .wrap.compact .device { width: 100%; }
  .wrap.compact .header { padding: 14px 16px 12px; }
  .wrap.compact .chips { margin-left: 0; }
  .footer {
    display: flex; gap: 14px; align-items: center; padding: 10px 18px 12px; font-size: 10px; color: var(--muted);
    border-top: 1px solid var(--line); flex-wrap: wrap;
  }
  .legend { display: inline-flex; align-items: center; gap: 5px; }
  .legend i { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
  .legend i.ok { background: var(--ok); } .legend i.bad { background: var(--bad); } .legend i.unknown { background: var(--unknown); }
  .empty { padding: 24px; text-align: center; color: var(--muted); }
  .empty.bad { color: var(--bad); }
`;

if (!customElements.get("gwn-topology-card")) {
  customElements.define("gwn-topology-card", GwnTopologyCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "gwn-topology-card",
    name: "GWN Network Topology",
    description: "Access points, switches with ports, gateway and uplinks of a GWN network",
    preview: true,
    documentationURL: "https://github.com/digitalbirdo/ha_gwn_manager",
  });
  console.info(`%c GWN-TOPOLOGY-CARD %c ${TOPOLOGY_VERSION} `, "background:#22d3ee;color:#0b1220", "");
}
