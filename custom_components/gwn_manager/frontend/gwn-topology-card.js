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
 *     Switch: Gateway:LAN1
 *   entry_id: ...            # optional, only one GWN Manager entry
 */

const TOPOLOGY_VERSION = "0.5.0";
const RESUBSCRIBE_MS = 90_000;
const WIRE_COLORS = ["#22d3ee", "#a78bfa", "#34d399", "#fbbf24", "#f472b6", "#60a5fa"];

const TEXT = {
  en: {
    topology: "Network topology", ap: "Access point", gateway: "Internet gateway", switch: "Switch",
    aps: "APs", switches: "Switches", clients: "Clients", online: "Online", offline: "Offline",
    ap1: "AP", switch1: "Switch", client1: "Client",
    unknown: "Unknown", internetOnline: "Internet online", internetOffline: "Internet offline",
    activePorts: "active ports", uplinkUnknown: "Uplink unknown", noData: "Waiting for data…",
    port: "Port",
  },
  de: {
    topology: "Netzwerk-Topologie", ap: "Access Point", gateway: "Internet-Gateway", switch: "Switch",
    aps: "APs", switches: "Switches", clients: "Clients", online: "Online", offline: "Offline",
    ap1: "AP", switch1: "Switch", client1: "Client",
    unknown: "Unbekannt", internetOnline: "Internet online", internetOffline: "Internet offline",
    activePorts: "aktive Ports", uplinkUnknown: "Uplink unbekannt", noData: "Warte auf Daten …",
    port: "Port",
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

function speedClass(port) {
  if (!port.up) return "down";
  if ((port.speed ?? 0) >= 2500) return "s2500";
  if ((port.speed ?? 0) >= 1000) return "s1000";
  if (port.speed) return "s100";
  return "up";
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

  /** Resolve the configured uplinks to { childMac: { parentMac, portLabel } }. */
  _uplinks(devices) {
    const byName = new Map();
    for (const d of devices) {
      byName.set(d.name.toLowerCase(), d);
      byName.set(d.mac.toLowerCase(), d);
    }
    const switches = devices.filter((d) => d.type === "SWITCH");
    const result = {};
    for (const [child, target] of Object.entries(this._config.uplinks || {})) {
      const device = byName.get(String(child).toLowerCase());
      if (!device) continue;
      const text = String(target);
      const split = text.lastIndexOf(":");
      let parent = split > 0 ? byName.get(text.slice(0, split).toLowerCase()) : switches[0];
      const port = split > 0 ? text.slice(split + 1) : text;
      // "Switch" etc. may also be the start of a MAC containing ":" – try the whole value too.
      if (!parent && split > 0) parent = switches[0];
      if (parent) result[device.mac] = { parent: parent.mac, port: port.trim() };
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

    const switches = devices.filter((d) => d.type === "SWITCH");
    const routers = devices.filter((d) => d.type === "ROUTER" || d.wan);
    const aps = devices.filter((d) => !switches.includes(d) && !routers.includes(d));
    const connected = clients.filter((c) => c.connected).length;
    const wan = routers.find((r) => r.wan)?.wan;
    const wireColor = new Map(Object.keys(uplinks).map((mac, i) => [mac, WIRE_COLORS[i % WIRE_COLORS.length]]));

    const plural = (n, key, one) => `${n} ${this._t(n === 1 ? one : key)}`;
    const chips = net
      ? `
        <span class="chip"><i></i>${esc(plural(aps.length, "aps", "ap1"))}</span>
        <span class="chip"><i></i>${esc(plural(switches.length, "switches", "switch1"))}</span>
        <span class="chip"><i></i>${esc(plural(connected, "clients", "client1"))}</span>
        ${wan ? `<span class="chip ${wan.connected ? "ok" : "bad"}"><i></i>${esc(this._t(wan.connected ? "internetOnline" : "internetOffline"))}</span>` : ""}`
      : "";

    const apCard = (ap) => {
      const total = Object.values(ap.bands || {}).reduce((a, b) => a + b, 0);
      const bands = Object.entries(ap.bands || {})
        .sort()
        .map(([band, n]) => `${esc(band.replace("G", " GHz"))}: <b>${n}</b>`)
        .join(" · ");
      const up = uplinks[ap.mac];
      const parent = up && devices.find((d) => d.mac === up.parent);
      return `
        <div class="device ap ${ap.online ? "" : "is-offline"}" data-mac="${esc(ap.mac)}" data-entity="${esc(ap.entity_id ?? "")}"
             style="--wire:${wireColor.get(ap.mac) || "var(--muted)"}">
          <div class="device-head">
            <span class="kind">◆ ${esc(this._t("ap"))}</span>
            <span class="pill ${ap.online ? "ok" : "bad"}">${esc(this._t(ap.online ? "online" : "offline"))}</span>
          </div>
          <div class="avatar"><ha-icon icon="mdi:access-point"></ha-icon></div>
          <div class="name">${esc(ap.name)}</div>
          <div class="model">${esc(ap.model ?? "")}${ap.area ? ` · ${esc(ap.area)}` : ""}</div>
          <div class="clients">◆ <b>${total}</b> ${esc(this._t("clients"))}</div>
          <div class="bands">${bands || "&nbsp;"}</div>
          <div class="uplink anchor-up">${up
            ? `◆ ${esc(parent?.name ?? "")} · ${esc(this._t("port"))} ${esc(up.port)}`
            : esc(this._t("uplinkUnknown"))}</div>
        </div>`;
    };

    const portBox = (device, port) => {
      const child = Object.entries(uplinks).find(([, u]) => u.parent === device.mac && u.port === port.label);
      const childDevice = child && devices.find((d) => d.mac === child[0]);
      const label = childDevice ? childDevice.name : port.clients ? `${port.clients} ${this._t("clients")}` : "";
      const speed = port.speed ? (port.speed >= 1000 ? `${port.speed / 1000}G` : `${port.speed}M`) : "";
      return `
        <div class="port ${speedClass(port)} ${child ? "linked" : ""}" data-device="${esc(device.mac)}" data-port="${esc(port.label)}"
             style="${child ? `--wire:${wireColor.get(child[0])}` : ""}" title="${esc(port.label)} ${esc(speed)}">
          <div class="port-box">
            <span class="num">${esc(port.label)}</span>
            ${port.poe ? `<span class="poe">⚡</span>` : ""}
            <span class="speed">${esc(speed)}</span>
          </div>
          <div class="port-label">${esc(label)}</div>
        </div>`;
    };

    const panel = (device, kind) => {
      const ports = (device.ports || []).filter((p) => this._config.show_all_ports || p.up || kind === "gateway");
      const active = (device.ports || []).filter((p) => p.up).length;
      const up = uplinks[device.mac];
      const wireless = Object.entries(device.bands || {});
      const wirelessInfo = wireless.length
        ? `<span class="muted">◆ <b>${wireless.reduce((a, [, n]) => a + n, 0)}</b> ${esc(this._t("clients"))} · ${wireless
            .sort().map(([band, n]) => `${esc(band.replace("G", " GHz"))}: ${n}`).join(" · ")}</span>`
        : "";
      const extra = kind === "gateway" && device.wan
        ? `<div class="wan">
             <span>↓ <b>${esc(formatRate(device.wan.downloadRate))}</b></span>
             <span>↑ <b>${esc(formatRate(device.wan.uploadRate))}</b></span>
             <span class="muted">${esc(device.wan.wanIp ?? "")}</span>
             <span class="muted">${esc(formatDuration(device.wan.connectDuration))}</span>
             ${wirelessInfo}
           </div>`
        : wirelessInfo ? `<div class="wan">${wirelessInfo}</div>` : "";
      return `
        <div class="panel ${kind} ${device.online ? "" : "is-offline"}" data-mac="${esc(device.mac)}" data-entity="${esc(device.entity_id ?? "")}"
             style="--wire:${wireColor.get(device.mac) || "var(--muted)"}">
          <div class="panel-head">
            <div class="avatar small"><ha-icon icon="${kind === "gateway" ? "mdi:router-network" : "mdi:switch"}"></ha-icon></div>
            <div>
              <div class="name">${esc(device.name)}</div>
              <div class="model">${esc(device.model ?? "")}${device.area ? ` · ${esc(device.area)}` : ""}</div>
            </div>
            <div class="spacer"></div>
            ${kind === "gateway" ? `<span class="kind">${esc(this._t("gateway"))}</span>` : ""}
            <span class="pill ${device.online ? "ok" : "bad"}">${esc(this._t(device.online ? "online" : "offline"))}</span>
            ${kind === "switch" ? `<span class="muted small">${active}/${(device.ports || []).length} ${esc(this._t("activePorts"))}</span>` : ""}
          </div>
          <div class="panel-body">
            ${extra}
            <div class="ports">${ports.map((p) => portBox(device, p)).join("")}</div>
          </div>
        </div>`;
    };

    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="wrap">
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
            ${switches.map((s) => panel(s, "switch")).join("")}
            ${routers.map((r) => panel(r, "gateway")).join("")}
          </div>
          <div class="footer">
            <span class="legend"><i class="ok"></i>${esc(this._t("online"))}</span>
            <span class="legend"><i class="bad"></i>${esc(this._t("offline"))}</span>
            <span class="legend"><i class="s2500"></i>2.5G</span>
            <span class="legend"><i class="s1000"></i>1G</span>
            <span class="legend"><i class="s100"></i>100M</span>
            <span class="spacer"></span>
            <span class="muted">GWN Topology</span>
          </div>
        </div>
      </ha-card>`;
    this._applyAppearance();
    for (const el of this.shadowRoot.querySelectorAll("[data-entity]")) {
      el.addEventListener("click", () => {
        const entityId = el.dataset.entity;
        if (entityId) {
          this.dispatchEvent(new CustomEvent("hass-more-info", { bubbles: true, composed: true, detail: { entityId } }));
        }
      });
    }
    requestAnimationFrame(() => this._drawWires());
  }

  _drawWires() {
    const svg = this.shadowRoot?.querySelector(".wires");
    const stage = this.shadowRoot?.querySelector(".stage");
    if (!svg || !stage || !this._uplinkMap) return;
    const base = stage.getBoundingClientRect();
    svg.setAttribute("width", base.width);
    svg.setAttribute("height", base.height);
    const paths = [];
    for (const [index, [childMac, up]] of Object.entries(this._uplinkMap).entries()) {
      const child = stage.querySelector(`[data-mac="${CSS.escape(childMac)}"]`);
      const port = stage.querySelector(
        `.port[data-device="${CSS.escape(up.parent)}"][data-port="${CSS.escape(up.port)}"] .port-box`,
      );
      if (!child || !port) continue;
      const from = child.getBoundingClientRect();
      const to = port.getBoundingClientRect();
      const color = getComputedStyle(child).getPropertyValue("--wire").trim() || "#22d3ee";
      const x1 = from.left + from.width / 2 - base.left;
      const x2 = to.left + to.width / 2 - base.left;
      let y1, y2;
      if (from.top < to.top) {
        // Device above the port it is plugged into (AP above the switch, switch above the gateway).
        y1 = from.bottom - base.top;
        y2 = to.top - base.top;
      } else {
        y1 = from.top - base.top;
        y2 = to.bottom - base.top;
      }
      // Run the horizontal part through the gap below/above the device.
      // Slightly offset per wire so parallel runs stay distinguishable.
      const mid = y1 + Math.sign(y2 - y1) * (9 + (index % 4) * 5);
      paths.push(`
        <path d="M${x1},${y1} V${mid} H${x2} V${y2}" class="wire-glow" stroke="${color}"/>
        <path d="M${x1},${y1} V${mid} H${x2} V${y2}" class="wire" stroke="${color}"/>
        <circle cx="${x2}" cy="${y2}" r="3" fill="${color}"/>`);
    }
    svg.innerHTML = paths.join("");
  }
}

const STYLE = `
  ha-card { overflow: hidden; }
  .wrap {
    position: relative; padding: 16px 18px 12px; color: var(--text); background: var(--bg);
    font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
  }
  .wrap.dark {
    --bg: radial-gradient(ellipse at 50% 0%, #15233a 0%, #0b1220 70%);
    --text: #e6edf7; --muted: #8597b2; --panel: rgba(255,255,255,.035); --panel-2: rgba(255,255,255,.06);
    --border: rgba(148,175,220,.16); --port: #0f1a2b; --ok: #34d399; --bad: #f87171; --unknown: #fbbf24;
    --glow: 1;
  }
  .wrap.light {
    --bg: linear-gradient(180deg, #f7f9fc 0%, #edf1f7 100%);
    --text: #1b2433; --muted: #66748b; --panel: #ffffff; --panel-2: #f3f6fb;
    --border: rgba(27,36,51,.1); --port: #eef2f8; --ok: #059669; --bad: #dc2626; --unknown: #d97706;
    --glow: 0;
  }
  .header { display: flex; align-items: flex-start; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
  .eyebrow { font-size: 10px; letter-spacing: 2px; text-transform: uppercase; color: #22d3ee; font-weight: 700; }
  .title { font-size: 18px; font-weight: 600; margin-top: 2px; }
  .chips { margin-left: auto; display: flex; gap: 6px; flex-wrap: wrap; }
  .chip {
    display: inline-flex; align-items: center; gap: 6px; font-size: 11px; padding: 4px 10px;
    border: 1px solid var(--border); border-radius: 999px; background: var(--panel); color: var(--muted);
  }
  .chip i { width: 6px; height: 6px; border-radius: 50%; background: #22d3ee; }
  .chip.ok { color: var(--ok); } .chip.ok i { background: var(--ok); box-shadow: 0 0 6px var(--ok); }
  .chip.bad { color: var(--bad); } .chip.bad i { background: var(--bad); }
  .stage { position: relative; display: flex; flex-direction: column; gap: 34px; }
  .wires { position: absolute; inset: 0; pointer-events: none; overflow: visible; z-index: 2; }
  .wire { fill: none; stroke-width: 2; stroke-dasharray: 6 6; animation: flow 1.6s linear infinite; }
  .wire-glow { fill: none; stroke-width: 6; opacity: calc(.18 * var(--glow)); }
  @keyframes flow { to { stroke-dashoffset: -24; } }
  .aps { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; position: relative; z-index: 1; }
  .device, .panel {
    position: relative; z-index: 1; background: var(--panel); border: 1px solid var(--border);
    border-radius: 14px; cursor: pointer; transition: border-color .2s, transform .2s;
  }
  .device:hover, .panel:hover { border-color: var(--wire); }
  .device { padding: 10px 12px 10px; text-align: center; }
  .device.is-offline, .panel.is-offline { opacity: .6; }
  .device-head, .panel-head { display: flex; align-items: center; gap: 8px; }
  .device-head { justify-content: space-between; }
  .kind { font-size: 10px; color: var(--muted); letter-spacing: .3px; }
  .device .kind { color: var(--wire); }
  .pill { font-size: 10px; padding: 2px 8px; border-radius: 999px; font-weight: 600; }
  .pill.ok { color: var(--ok); background: color-mix(in srgb, var(--ok) 14%, transparent); }
  .pill.bad { color: var(--bad); background: color-mix(in srgb, var(--bad) 14%, transparent); }
  .avatar {
    width: 46px; height: 46px; margin: 8px auto 6px; border-radius: 50%; display: grid; place-items: center;
    background: radial-gradient(circle at 35% 30%, #ffffff, #dbe7f5); color: #0b5b8a;
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--wire) 55%, transparent),
                0 0 calc(18px * var(--glow)) color-mix(in srgb, var(--wire) 60%, transparent);
  }
  .avatar.small { width: 30px; height: 30px; margin: 0; }
  .avatar ha-icon { --mdc-icon-size: 24px; }
  .avatar.small ha-icon { --mdc-icon-size: 18px; }
  .name { font-weight: 600; font-size: 13px; }
  .model { font-size: 11px; color: var(--muted); }
  .clients { font-size: 12px; margin-top: 6px; }
  .clients, .device .uplink { color: var(--muted); }
  .clients b { color: var(--text); }
  .bands { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .bands b { color: var(--text); }
  .uplink { font-size: 10px; margin-top: 8px; padding-top: 6px; border-top: 1px dashed var(--border); }
  .panel { padding: 10px 12px 12px; }
  .panel-head { margin-bottom: 10px; }
  .spacer { flex: 1; }
  .muted { color: var(--muted); }
  .small { font-size: 11px; }
  .panel-body { background: var(--panel-2); border-radius: 10px; padding: 10px; display: flex; gap: 14px; align-items: flex-start; flex-wrap: wrap; }
  .wan { display: flex; flex-direction: column; gap: 2px; font-size: 12px; min-width: 130px; }
  .ports { display: flex; flex-wrap: wrap; gap: 8px; flex: 1; }
  .port { width: 64px; text-align: center; }
  .port-box {
    position: relative; height: 30px; border-radius: 6px; background: var(--port);
    border: 1.5px solid var(--border); display: flex; align-items: center; justify-content: center; gap: 3px;
    font-size: 11px; font-weight: 600;
  }
  .port.s2500 .port-box { border-color: #22d3ee; box-shadow: 0 0 calc(8px * var(--glow)) #22d3ee66; }
  .port.s1000 .port-box { border-color: #34d399; box-shadow: 0 0 calc(8px * var(--glow)) #34d39966; }
  .port.s100 .port-box { border-color: #fbbf24; }
  .port.up .port-box { border-color: var(--ok); }
  .port.down .port-box { opacity: .45; }
  .port.linked .port-box { border-color: var(--wire); box-shadow: 0 0 calc(10px * var(--glow)) var(--wire); }
  .port .speed { position: absolute; bottom: 1px; right: 4px; font-size: 8px; color: var(--muted); font-weight: 500; }
  .port .poe { font-size: 10px; }
  .port-label { font-size: 9px; color: var(--muted); margin-top: 3px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .footer { display: flex; gap: 12px; align-items: center; margin-top: 14px; font-size: 10px; color: var(--muted); flex-wrap: wrap; }
  .legend { display: inline-flex; align-items: center; gap: 5px; }
  .legend i { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
  .legend i.ok { background: var(--ok); } .legend i.bad { background: var(--bad); }
  .legend i.s2500 { background: #22d3ee; } .legend i.s1000 { background: #34d399; } .legend i.s100 { background: #fbbf24; }
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
