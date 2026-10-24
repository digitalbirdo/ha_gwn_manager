/*
 * GWN Network Map card for Home Assistant.
 *
 * Live, force-directed map of the GWN Manager network: access points and their
 * clients. Served by the gwn_manager integration, data comes from the
 * "gwn_manager/subscribe_topology" WebSocket subscription.
 *
 *   type: custom:gwn-network-card
 *   title: Netzwerk          # optional
 *   height: 450              # px, optional
 *   show_offline: false      # also show disconnected clients
 *   show_labels: true        # show client names (false: only on hover)
 *   animate: true            # gentle floating of the bubbles
 *   color_by: rssi           # rssi or band; can also be switched on the card
 *   show_rssi: true          # signal strength (dBm) on the client links; toggle with "dBm"
 *   appearance: auto         # auto (follows HA dark mode), dark or light
 *   layout: auto             # floors (APs arranged by the floor of their area), free, or auto
 *                            # (floors as soon as an AP's area is assigned to a floor)
 *   entry_id: ...            # optional, only one GWN Manager entry
 */

const VERSION = "0.5.6";

const RADIUS = { root: 20, ap: 15, client: 7 };
const CHARGE = { root: 3, ap: 2.6, client: 1 };
const REPULSION = 700;
const SPRING = 0.05;
const DAMPING = 0.82;
const GRAVITY = 0.004;
const ALPHA_MIN = 0.04;
const RESUBSCRIBE_AFTER_MS = 90_000;

const SVG_NS = "http://www.w3.org/2000/svg";

function rssiColor(rssi) {
  if (rssi === null || rssi === undefined) return "var(--disabled-text-color, #9e9e9e)";
  if (rssi >= -60) return "var(--success-color, #43a047)";
  if (rssi >= -70) return "#9ccc65";
  if (rssi >= -80) return "var(--warning-color, #ffa600)";
  return "var(--error-color, #db4437)";
}

const BAND_COLORS = {
  "2.4G": "#ff9800",
  "5G": "#1e88e5",
  "6G": "#8e24aa",
};

function bandColor(band) {
  return BAND_COLORS[band] || "var(--disabled-text-color, #9e9e9e)";
}

function linkLength(rssi) {
  // Stronger signal = shorter link: -30 dBm -> 45 px, -90 dBm -> 165 px
  if (rssi === null || rssi === undefined) return 120;
  return 45 + Math.min(Math.max(-rssi - 30, 0), 60) * 2;
}

const STRINGS = {
  en: { wired: "Wired", channel: "Channel", offline: "Offline", clients: "clients", signal: "Signal", band: "Band" },
  de: { wired: "Kabel", channel: "Kanal", offline: "Offline", clients: "Clients", signal: "Signal", band: "Band" },
};

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

class GwnNetworkCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._nodes = new Map();
    this._links = [];
    // Floors of the APs, top floor first: [{ id, name, level }]
    this._floors = [];
    this._alpha = 1;
    this._view = { x: 0, y: 0, k: 1 };
    this._autoFit = true;
    this._width = 400;
    this._frame = null;
    this._unsub = null;
    this._lastMessage = 0;
  }

  static getStubConfig() {
    return { show_offline: false };
  }

  setConfig(config) {
    this._config = {
      height: 450,
      show_offline: false,
      show_labels: true,
      show_rssi: true,
      animate: true,
      color_by: "rssi",
      appearance: "auto",
      layout: "auto",
      ...config,
    };
    this._colorBy = this._config.color_by === "band" ? "band" : "rssi";
    this._showRssi = this._config.show_rssi !== false;
    // Labels need more room between the bubbles.
    this._spread = this._config.show_labels ? 2.2 : 1;
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    this._applyAppearance();
    if (first && this.isConnected) this._subscribe();
  }

  _applyAppearance() {
    const wrap = this.shadowRoot?.querySelector(".wrap");
    if (!wrap) return;
    const mode = this._config.appearance;
    const dark = mode === "dark" || (mode !== "light" && Boolean(this._hass?.themes?.darkMode));
    wrap.classList.toggle("dark", dark);
    wrap.classList.toggle("light", !dark);
  }

  getCardSize() {
    return Math.ceil(this._config.height / 50) + 1;
  }

  connectedCallback() {
    if (this._hass && !this._unsub) this._subscribe();
    this._startLoop();
    this._resizeObserver = new ResizeObserver(() => this._resize());
    this._resizeObserver.observe(this);
  }

  disconnectedCallback() {
    this._unsubscribe();
    cancelAnimationFrame(this._frame);
    this._frame = null;
    this._resizeObserver?.disconnect();
  }

  // ---------------------------------------------------------------- data

  async _subscribe() {
    this._unsubscribe();
    const msg = { type: "gwn_manager/subscribe_topology" };
    if (this._config.entry_id) msg.entry_id = this._config.entry_id;
    try {
      this._unsub = await this._hass.connection.subscribeMessage((data) => {
        this._lastMessage = Date.now();
        this._error(null);
        this._update(data.networks);
      }, msg);
      this._lastMessage = Date.now();
    } catch (err) {
      this._error(err.message || String(err));
    }
  }

  _unsubscribe() {
    if (this._unsub) {
      this._unsub().catch?.(() => {});
      this._unsub = null;
    }
  }

  _update(networks) {
    const seen = new Set();
    const links = [];
    const center = { x: this._width / 2, y: this._config.height / 2 };

    const upsert = (id, type, data, near) => {
      seen.add(id);
      let node = this._nodes.get(id);
      if (!node) {
        const base = near || center;
        const angle = Math.random() * Math.PI * 2;
        const dist = type === "root" ? 0 : type === "ap" ? 80 : 40;
        node = {
          id, type,
          x: base.x + Math.cos(angle) * dist,
          y: base.y + Math.sin(angle) * dist,
          vx: 0, vy: 0,
        };
        this._nodes.set(id, node);
        this._alpha = 1;
      }
      if (type === "client" && node.data && node.data.ap !== data.ap) this._alpha = Math.max(this._alpha, 0.6);
      node.data = data;
      return node;
    };

    for (const net of networks) {
      const root = upsert(`net:${net.entry_id}`, "root", { name: net.title });
      // Devices hang off their physical uplink (switch/router) if known,
      // otherwise off the GWN Manager.
      const apNodes = new Map();
      for (const ap of net.aps) apNodes.set(ap.mac, upsert(`ap:${ap.mac}`, "ap", ap, root));
      for (const ap of net.aps) {
        const parent = ap.uplink?.parent && apNodes.get(ap.uplink.parent);
        links.push({
          source: parent || root,
          target: apNodes.get(ap.mac),
          length: parent ? (this._config.show_labels ? 170 : 120) : this._config.show_labels ? 210 : 150,
          strength: 1,
          lan: Boolean(parent),
          port: parent ? ap.uplink.port : null,
        });
      }
      for (const client of net.clients) {
        if (!client.connected && !this._config.show_offline) continue;
        const apNode = client.connected && client.ap ? this._nodes.get(`ap:${client.ap}`) : null;
        const parent = apNode || root;
        const node = upsert(`cl:${client.mac}`, "client", client, parent);
        links.push({
          source: parent,
          target: node,
          length: (apNode ? linkLength(client.rssi) : 230) * (this._config.show_labels ? 1.35 : 1),
          strength: apNode ? 1 : 0.3,
          rssi: client.rssi,
          offline: !client.connected,
        });
      }
    }

    this._updateFloors(networks);

    for (const id of [...this._nodes.keys()]) {
      if (!seen.has(id)) {
        this._nodes.delete(id);
        this._alpha = Math.max(this._alpha, 0.5);
      }
    }
    this._links = links;
    this._syncElements();
  }

  _updateFloors(networks) {
    const floors = new Map();
    for (const net of networks) {
      for (const ap of net.aps) {
        if (ap.floor_id && !floors.has(ap.floor_id)) {
          floors.set(ap.floor_id, { id: ap.floor_id, name: ap.floor, level: ap.floor_level });
        }
      }
    }
    // Highest floor at the top; floors without a level below, by name.
    const sorted = [...floors.values()].sort((a, b) =>
      (b.level ?? -Infinity) - (a.level ?? -Infinity) || String(a.name).localeCompare(String(b.name)));
    const layout = this._config.layout;
    const useFloors = layout !== "free" && sorted.length > 0;
    const changed = JSON.stringify(sorted) !== JSON.stringify(this._floors) || useFloors !== this._floorLayout;
    this._floors = useFloors ? sorted : [];
    this._floorLayout = useFloors;
    const index = new Map(this._floors.map((f, i) => [f.id, i]));
    // A device without a floor of its own (e.g. a switch without an area) takes
    // the floor of the device it is plugged into; its LAN clients follow it.
    const floorOf = (node, depth = 0) => {
      const own = index.get(node.data.floor_id);
      if (own !== undefined || depth > 5) return own;
      const parent = node.data.uplink?.parent && this._nodes.get(`ap:${node.data.uplink.parent}`);
      return parent ? floorOf(parent, depth + 1) : undefined;
    };
    for (const node of this._nodes.values()) {
      if (node.type === "ap") node.floor = floorOf(node);
    }
    if (changed) {
      this._alpha = 1;
      this._syncFloors();
    }
  }

  _floorGap() {
    // Use the card height, but leave enough room for the client clusters.
    const perFloor = (this._config.height - 40) / Math.max(this._floors.length, 1);
    return Math.max(this._config.show_labels ? 210 : 160, perFloor);
  }

  _floorY(index) {
    const cy = this._config.height / 2;
    return cy + (index - (this._floors.length - 1) / 2) * this._floorGap();
  }

  // ---------------------------------------------------------- simulation

  _tick() {
    const nodes = [...this._nodes.values()];
    const alpha = this._alpha;
    const cx = this._width / 2;
    const cy = this._config.height / 2;

    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
        const d = Math.sqrt(d2);
        let f = (REPULSION * CHARGE[a.type] * CHARGE[b.type] * this._spread) / d2;
        const minDist = RADIUS[a.type] + RADIUS[b.type] + (this._config.show_labels ? 14 : 4);
        if (d < minDist) f += (minDist - d) * 0.5;
        const fx = (dx / d) * f * alpha;
        const fy = (dy / d) * f * alpha;
        a.vx -= fx; a.vy -= fy;
        b.vx += fx; b.vy += fy;
      }
    }

    for (const link of this._links) {
      const { source: s, target: t } = link;
      const dx = t.x - s.x;
      const dy = t.y - s.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (d - link.length) * SPRING * link.strength * alpha;
      const fx = (dx / d) * f;
      const fy = (dy / d) * f;
      // Children move more than parents, which keeps the tree tidy.
      t.vx -= fx * 0.8; t.vy -= fy * 0.8;
      s.vx += fx * 0.2; s.vy += fy * 0.2;
    }

    if (this._floorLayout) {
      for (const n of nodes) {
        if (n.type === "ap" && n.floor !== undefined) {
          // Keep APs on their floor, regardless of how settled the layout is.
          n.vy += (this._floorY(n.floor) - n.y) * 0.08;
        } else if (n.type === "client") {
          // Clients move freely within their AP's floor, but stay inside its band.
          const ap = this._nodes.get(`ap:${n.data.ap}`);
          if (ap?.floor !== undefined) {
            const dy = n.y - this._floorY(ap.floor);
            const limit = this._floorGap() / 2 - 24;
            if (Math.abs(dy) > limit) n.vy -= (dy - Math.sign(dy) * limit) * 0.2;
          }
        } else if (n.type === "root") {
          // The GWN Manager sits to the left of the floors.
          n.vx += (cx - 420 - n.x) * 0.05;
        }
      }
    }

    const gravityX = this._floorLayout ? GRAVITY * 0.3 : GRAVITY;
    for (const n of nodes) {
      n.vx += (cx - n.x) * gravityX * alpha * (n.type === "root" ? 5 : 1);
      n.vy += (cy - n.y) * GRAVITY * alpha * (n.type === "root" ? 5 : 1);
      if (this._config.animate && n.type === "client") {
        n.vx += (Math.random() - 0.5) * 0.25;
        n.vy += (Math.random() - 0.5) * 0.25;
      }
      if (n.fx !== undefined) {
        n.x = n.fx; n.y = n.fy; n.vx = 0; n.vy = 0;
        continue;
      }
      n.vx *= DAMPING;
      n.vy *= DAMPING;
      n.x += n.vx;
      n.y += n.vy;
    }

    this._alpha = Math.max(ALPHA_MIN, this._alpha * 0.985);
  }

  _startLoop() {
    if (this._frame) return;
    const loop = () => {
      this._frame = requestAnimationFrame(loop);
      if (document.hidden) return;
      if (this._hass && Date.now() - this._lastMessage > RESUBSCRIBE_AFTER_MS) {
        // E.g. the integration was reloaded; the old subscription is dead.
        this._lastMessage = Date.now();
        this._subscribe();
      }
      if (!this._nodes.size) return;
      this._tick();
      this._draw();
    };
    this._frame = requestAnimationFrame(loop);
  }

  // ------------------------------------------------------------ rendering

  _render() {
    if (!this.shadowRoot) return;
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { overflow: hidden; }
        .wrap {
          position: relative; height: ${Number(this._config.height)}px; touch-action: none;
          color: var(--gwn-text);
          background: var(--gwn-bg);
          transition: background .4s;
        }
        .wrap.light {
          --gwn-bg: radial-gradient(ellipse at 50% 35%, #ffffff 0%, #eef2f9 100%);
          --gwn-text: #1c2333;
          --gwn-muted: #5f6b80;
          --gwn-halo: rgba(255, 255, 255, .9);
          --gwn-chip-bg: rgba(255, 255, 255, .7);
          --gwn-chip-border: rgba(28, 35, 51, .1);
          --gwn-backbone: rgba(28, 35, 51, .16);
          --gwn-node-stroke: #ffffff;
          --gwn-tooltip-bg: rgba(255, 255, 255, .92);
          --gwn-shadow: 0 6px 24px rgba(28, 35, 51, .14);
          --gwn-node-filter: drop-shadow(0 1px 2px rgba(28, 35, 51, .25));
          --gwn-link-filter: none;
          --gwn-floor-a: rgba(28, 35, 51, .045);
          --gwn-floor-b: rgba(28, 35, 51, .015);
        }
        .wrap.dark {
          --gwn-bg: radial-gradient(ellipse at 50% 35%, #1d2742 0%, #0c111d 75%);
          --gwn-text: #e8edff;
          --gwn-muted: #8f9bb8;
          --gwn-halo: rgba(12, 17, 29, .85);
          --gwn-chip-bg: rgba(255, 255, 255, .06);
          --gwn-chip-border: rgba(255, 255, 255, .12);
          --gwn-backbone: rgba(140, 170, 255, .22);
          --gwn-node-stroke: rgba(255, 255, 255, .35);
          --gwn-tooltip-bg: rgba(18, 24, 40, .88);
          --gwn-shadow: 0 8px 32px rgba(0, 0, 0, .5);
          --gwn-node-filter: url(#gwn-glow);
          --gwn-link-filter: url(#gwn-glow-soft);
          --gwn-floor-a: rgba(140, 170, 255, .07);
          --gwn-floor-b: rgba(140, 170, 255, .025);
        }
        .title {
          position: absolute; left: 16px; top: 12px; z-index: 1;
          font-size: 18px; font-weight: 500; letter-spacing: .2px; pointer-events: none;
        }
        svg { width: 100%; height: 100%; display: block; cursor: grab; user-select: none; }
        svg.panning { cursor: grabbing; }
        .link { stroke-linecap: round; filter: var(--gwn-link-filter); }
        .link.backbone { stroke: var(--gwn-backbone); stroke-dasharray: 2 6; }
        .link.lan { stroke: #38c6e8; stroke-opacity: .75; }
        .link.offline { stroke-dasharray: 3 4; opacity: .35; }
        .floor rect { fill: var(--gwn-floor-b); }
        .floor:nth-child(odd) rect { fill: var(--gwn-floor-a); }
        .floor text {
          font: 600 12px var(--paper-font-body1_-_font-family, sans-serif);
          letter-spacing: 1.5px; text-transform: uppercase; fill: var(--gwn-muted); opacity: .8;
        }
        .node { cursor: pointer; }
        .node circle.body {
          stroke: var(--gwn-node-stroke); stroke-width: 1.5;
          filter: var(--gwn-node-filter); transition: r .3s;
        }
        .node.root circle.body { fill: url(#gwn-root); }
        .node.ap circle.body { fill: url(#gwn-ap); }
        .node.ap.offline circle.body { fill: url(#gwn-offline); }
        .node.client.offline circle.body { opacity: .4; }
        .node circle.pulse {
          fill: none; stroke: #4fc3ff; stroke-width: 2; opacity: 0; pointer-events: none;
          transform-box: fill-box; transform-origin: center;
          animation: gwn-pulse 3.2s ease-out infinite;
        }
        .node.ap.offline circle.pulse { stroke: #ff6b6b; }
        @keyframes gwn-pulse {
          0% { transform: scale(1); opacity: .55; }
          100% { transform: scale(1.9); opacity: 0; }
        }
        .node text {
          font: 11px var(--paper-font-body1_-_font-family, sans-serif);
          fill: var(--gwn-text); paint-order: stroke;
          stroke: var(--gwn-halo); stroke-width: 3px; stroke-linejoin: round;
          pointer-events: none;
        }
        .node.ap text, .node.root text { font-weight: 600; font-size: 12px; }
        .node.client text { display: ${this._config.show_labels ? "block" : "none"}; fill: var(--gwn-muted); }
        .node.client:hover text, .node.client.hover text { display: block; fill: var(--gwn-text); }
        .node .count { font-size: 10px; fill: #fff; stroke: none; font-weight: 700; }
        .tooltip {
          position: absolute; pointer-events: none; display: none; z-index: 2;
          background: var(--gwn-tooltip-bg); color: var(--gwn-text);
          border: 1px solid var(--gwn-chip-border); border-radius: 12px;
          box-shadow: var(--gwn-shadow); padding: 10px 12px;
          backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
          font-size: 12px; line-height: 1.55; max-width: 260px;
        }
        .tooltip b { font-size: 13px; }
        .tooltip .muted { color: var(--gwn-muted); }
        .chip {
          position: absolute; bottom: 10px; display: flex; align-items: center;
          background: var(--gwn-chip-bg); border: 1px solid var(--gwn-chip-border);
          border-radius: 999px; backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
          font-size: 11px; color: var(--gwn-muted);
        }
        .legend { left: 12px; gap: 12px; padding: 4px 12px; pointer-events: none; }
        .legend span::before {
          content: ""; display: inline-block; width: 8px; height: 8px; border-radius: 50%;
          margin-right: 5px; background: var(--c); box-shadow: 0 0 6px var(--c); vertical-align: 0;
        }
        .modes { right: 12px; padding: 2px; gap: 2px; }
        .modes button {
          border: 0; background: transparent; color: var(--gwn-muted); border-radius: 999px;
          font-size: 11px; padding: 4px 12px; cursor: pointer; transition: background .2s, color .2s;
        }
        .modes .sep { width: 1px; align-self: stretch; margin: 3px 2px; background: var(--gwn-chip-border); }
        .rssi {
          font: 600 9px var(--paper-font-body1_-_font-family, sans-serif);
          paint-order: stroke; stroke: var(--gwn-halo); stroke-width: 3px; stroke-linejoin: round;
          pointer-events: none;
        }
        .modes button.active { background: var(--primary-color); color: var(--text-primary-color, #fff); }
        .error { padding: 16px; color: var(--error-color, #db4437); }
      </style>
      <ha-card>
        <div class="wrap">
          ${this._config.title ? `<div class="title">${escapeHtml(this._config.title)}</div>` : ""}
          <svg>
            <defs>
              <radialGradient id="gwn-ap" cx="35%" cy="30%" r="75%">
                <stop offset="0%" stop-color="#6fd3ff"/>
                <stop offset="100%" stop-color="#0277bd"/>
              </radialGradient>
              <radialGradient id="gwn-root" cx="35%" cy="30%" r="75%">
                <stop offset="0%" stop-color="#b39dff"/>
                <stop offset="100%" stop-color="#4527a0"/>
              </radialGradient>
              <radialGradient id="gwn-offline" cx="35%" cy="30%" r="75%">
                <stop offset="0%" stop-color="#ff8a80"/>
                <stop offset="100%" stop-color="#b71c1c"/>
              </radialGradient>
              <filter id="gwn-glow" x="-100%" y="-100%" width="300%" height="300%">
                <feGaussianBlur stdDeviation="3" result="blur"/>
                <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
              </filter>
              <filter id="gwn-glow-soft" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="1.5" result="blur"/>
                <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
              </filter>
            </defs>
            <g class="viewport">
              <g class="floors"></g>
              <g class="links"></g>
              <g class="rssi-labels"></g>
              <g class="nodes"></g>
            </g>
          </svg>
          <div class="tooltip"></div>
          <div class="chip legend"></div>
          <div class="chip modes">
            <button data-mode="rssi">Signal</button>
            <button data-mode="band">Band</button>
            <span class="sep"></span>
            <button data-toggle="rssi">dBm</button>
          </div>
        </div>
        <div class="error" hidden></div>
      </ha-card>`;
    this._applyAppearance();
    this._svg = this.shadowRoot.querySelector("svg");
    this._viewport = this.shadowRoot.querySelector(".viewport");
    this._linkLayer = this.shadowRoot.querySelector(".links");
    this._rssiLayer = this.shadowRoot.querySelector(".rssi-labels");
    this._rssiElements = [];
    this._floorLayer = this.shadowRoot.querySelector(".floors");
    this._floorElements = [];
    this._nodeLayer = this.shadowRoot.querySelector(".nodes");
    this._tooltip = this.shadowRoot.querySelector(".tooltip");
    this._elements = new Map();
    this._linkElements = [];
    const rssiButton = this.shadowRoot.querySelector('[data-toggle="rssi"]');
    rssiButton.classList.toggle("active", this._showRssi);
    rssiButton.addEventListener("click", () => {
      this._showRssi = !this._showRssi;
      rssiButton.classList.toggle("active", this._showRssi);
      this._syncElements();
    });
    for (const button of this.shadowRoot.querySelectorAll(".modes button[data-mode]")) {
      button.addEventListener("click", () => {
        this._colorBy = button.dataset.mode;
        this._renderLegend();
        this._syncElements();
      });
    }
    this._renderLegend();
    this._setupPanZoom();
    this._syncFloors();
    this._resize();
    if (this._nodes.size) this._syncElements();
  }

  _renderLegend() {
    const legend = this.shadowRoot.querySelector(".legend");
    const items = this._colorBy === "band"
      ? Object.entries(BAND_COLORS).map(([band, color]) => [color, band.replace("G", " GHz")])
      : [
          ["var(--success-color, #43a047)", "≥ −60 dBm"],
          ["#9ccc65", "≥ −70"],
          ["var(--warning-color, #ffa600)", "≥ −80"],
          ["var(--error-color, #db4437)", "< −80"],
        ];
    legend.innerHTML = items
      .map(([color, label]) => `<span style="--c: ${color}">${escapeHtml(label)}</span>`)
      .join("");
    for (const button of this.shadowRoot.querySelectorAll(".modes button[data-mode]")) {
      button.classList.toggle("active", button.dataset.mode === this._colorBy);
    }
  }

  _resize() {
    const width = this.shadowRoot?.querySelector(".wrap")?.clientWidth;
    if (width) this._width = width;
  }

  _error(message) {
    const el = this.shadowRoot?.querySelector(".error");
    if (!el) return;
    el.hidden = !message;
    el.textContent = message ? `GWN Manager: ${message}` : "";
  }

  _t(key) {
    const lang = (this._hass?.language || "en").split("-")[0];
    return (STRINGS[lang] || STRINGS.en)[key];
  }

  _clientColor(data) {
    return this._colorBy === "band" ? bandColor(data.band) : rssiColor(data.rssi);
  }

  _syncElements() {
    if (!this._nodeLayer) return;

    // Nodes
    for (const [id, el] of this._elements) {
      if (!this._nodes.has(id)) {
        el.remove();
        this._elements.delete(id);
      }
    }
    for (const node of this._nodes.values()) {
      let el = this._elements.get(node.id);
      if (!el) {
        el = document.createElementNS(SVG_NS, "g");
        if (node.type === "ap") {
          // Animated ring behind the AP; staggered so they don't pulse in sync.
          const pulse = document.createElementNS(SVG_NS, "circle");
          pulse.setAttribute("class", "pulse");
          pulse.style.animationDelay = `${(Math.random() * 3).toFixed(2)}s`;
          el.appendChild(pulse);
        }
        const body = document.createElementNS(SVG_NS, "circle");
        body.setAttribute("class", "body");
        el.appendChild(body);
        const label = document.createElementNS(SVG_NS, "text");
        label.setAttribute("class", "label");
        label.setAttribute("text-anchor", "middle");
        el.appendChild(label);
        if (node.type === "ap") {
          const count = document.createElementNS(SVG_NS, "text");
          count.setAttribute("class", "count");
          count.setAttribute("text-anchor", "middle");
          count.setAttribute("dy", "3.5");
          el.appendChild(count);
        }
        this._setupNodeEvents(el, node.id);
        this._nodeLayer.appendChild(el);
        this._elements.set(node.id, el);
      }
      const d = node.data;
      const offline = node.type === "ap" ? !d.online : node.type === "client" && !d.connected;
      el.setAttribute("class", `node ${node.type}${offline ? " offline" : ""}`);
      const circle = el.querySelector(".body");
      const label = el.querySelector(".label");
      let r = RADIUS[node.type];
      if (node.type === "ap") {
        const n = this._links.filter((l) => l.source === node).length;
        r += Math.min(Math.sqrt(n) * 2, 10);
        el.querySelector(".count").textContent = n || "";
        el.querySelector(".pulse").setAttribute("r", r);
      }
      if (node.type === "client") circle.setAttribute("fill", this._clientColor(d));
      circle.setAttribute("r", r);
      label.setAttribute("dy", r + 13);
      label.textContent = d.name;
      node.r = r;
    }

    // Links
    while (this._linkElements.length > this._links.length) this._linkElements.pop().remove();
    while (this._linkElements.length < this._links.length) {
      const line = document.createElementNS(SVG_NS, "line");
      this._linkLayer.appendChild(line);
      this._linkElements.push(line);
    }
    this._links.forEach((link, i) => {
      const line = this._linkElements[i];
      const isClient = link.target.type === "client";
      line.setAttribute("class", `link${isClient ? "" : link.lan ? " lan" : " backbone"}${link.offline ? " offline" : ""}`);
      if (isClient) line.setAttribute("stroke", this._clientColor(link.target.data));
      else line.removeAttribute("stroke");
      line.setAttribute("stroke-width", isClient ? 1.5 : 3);
      line.setAttribute("stroke-opacity", isClient ? 0.7 : 1);
    });

    // Signal strength on the client links
    const labelled = this._showRssi
      ? this._links.filter((l) => l.target.type === "client" && l.target.data.rssi != null)
      : [];
    while (this._rssiElements.length > labelled.length) this._rssiElements.pop().el.remove();
    while (this._rssiElements.length < labelled.length) {
      const el = document.createElementNS(SVG_NS, "text");
      el.setAttribute("class", "rssi");
      el.setAttribute("text-anchor", "middle");
      el.setAttribute("dy", "3");
      this._rssiLayer.appendChild(el);
      this._rssiElements.push({ el, link: null });
    }
    labelled.forEach((link, i) => {
      const item = this._rssiElements[i];
      item.link = link;
      item.el.textContent = `${link.target.data.rssi}`;
      item.el.setAttribute("fill", this._clientColor(link.target.data));
    });
  }

  _syncFloors() {
    if (!this._floorLayer) return;
    this._floorLayer.replaceChildren();
    this._floorElements = this._floors.map((floor) => {
      const g = document.createElementNS(SVG_NS, "g");
      g.setAttribute("class", "floor");
      const rect = document.createElementNS(SVG_NS, "rect");
      rect.setAttribute("rx", 18);
      const label = document.createElementNS(SVG_NS, "text");
      label.textContent = floor.name;
      g.append(rect, label);
      this._floorLayer.appendChild(g);
      return g;
    });
  }

  _nodeBounds() {
    let minX = Infinity, maxX = -Infinity;
    for (const n of this._nodes.values()) {
      minX = Math.min(minX, n.x - 60);
      maxX = Math.max(maxX, n.x + 60);
    }
    return { minX, maxX };
  }

  _draw() {
    if (this._floorElements.length) {
      const { minX, maxX } = this._nodeBounds();
      const gap = this._floorGap();
      this._floorElements.forEach((g, i) => {
        const y = this._floorY(i) - gap / 2 + 6;
        const rect = g.firstChild;
        rect.setAttribute("x", (minX - 30).toFixed(1));
        rect.setAttribute("y", y.toFixed(1));
        rect.setAttribute("width", (maxX - minX + 60).toFixed(1));
        rect.setAttribute("height", (gap - 12).toFixed(1));
        g.lastChild.setAttribute("x", (minX - 14).toFixed(1));
        g.lastChild.setAttribute("y", (y + 22).toFixed(1));
      });
    }
    for (const node of this._nodes.values()) {
      this._elements.get(node.id)?.setAttribute(
        "transform", `translate(${node.x.toFixed(1)},${node.y.toFixed(1)})`,
      );
    }
    this._links.forEach((link, i) => {
      const line = this._linkElements[i];
      if (!line) return;
      line.setAttribute("x1", link.source.x.toFixed(1));
      line.setAttribute("y1", link.source.y.toFixed(1));
      line.setAttribute("x2", link.target.x.toFixed(1));
      line.setAttribute("y2", link.target.y.toFixed(1));
    });
    for (const { el, link } of this._rssiElements) {
      if (!link) continue;
      // A bit closer to the client than the middle, so labels of one AP don't pile up.
      el.setAttribute("x", (link.source.x * 0.4 + link.target.x * 0.6).toFixed(1));
      el.setAttribute("y", (link.source.y * 0.4 + link.target.y * 0.6).toFixed(1));
    }
    if (this._autoFit) this._fit();
    const { x, y, k } = this._view;
    this._viewport.setAttribute("transform", `translate(${x},${y}) scale(${k})`);
  }

  _fit() {
    // Smoothly zoom/pan so that all bubbles (and their labels) are visible.
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of this._nodes.values()) {
      const r = (n.r || RADIUS[n.type]) + (this._config.show_labels || n.type !== "client" ? 45 : 10);
      minX = Math.min(minX, n.x - r);
      maxX = Math.max(maxX, n.x + r);
      minY = Math.min(minY, n.y - r);
      maxY = Math.max(maxY, n.y + r + 10);
    }
    if (!Number.isFinite(minX)) return;
    if (this._floorLayout && this._floors.length) {
      const gap = this._floorGap();
      minX -= 30;
      maxX += 30;
      minY = Math.min(minY, this._floorY(0) - gap / 2);
      maxY = Math.max(maxY, this._floorY(this._floors.length - 1) + gap / 2);
    }
    const height = this._config.height - 24; // keep the legend free
    const k = Math.min(1.5, this._width / (maxX - minX), height / (maxY - minY));
    const target = {
      k,
      x: this._width / 2 - ((minX + maxX) / 2) * k,
      y: height / 2 - ((minY + maxY) / 2) * k,
    };
    const ease = 0.08;
    this._view.k += (target.k - this._view.k) * ease;
    this._view.x += (target.x - this._view.x) * ease;
    this._view.y += (target.y - this._view.y) * ease;
  }

  // ---------------------------------------------------------- interaction

  _toGraph(event) {
    const rect = this._svg.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left - this._view.x) / this._view.k,
      y: (event.clientY - rect.top - this._view.y) / this._view.k,
    };
  }

  _setupNodeEvents(el, id) {
    let drag = null;
    el.addEventListener("pointerdown", (event) => {
      event.stopPropagation();
      const node = this._nodes.get(id);
      if (!node) return;
      el.setPointerCapture(event.pointerId);
      drag = { moved: false, startX: event.clientX, startY: event.clientY };
      node.fx = node.x;
      node.fy = node.y;
    });
    el.addEventListener("pointermove", (event) => {
      const node = this._nodes.get(id);
      if (!node) return;
      if (!drag) {
        this._showTooltip(node, event);
        return;
      }
      if (Math.abs(event.clientX - drag.startX) + Math.abs(event.clientY - drag.startY) > 4) {
        drag.moved = true;
        this._hideTooltip();
      }
      const p = this._toGraph(event);
      node.fx = p.x;
      node.fy = p.y;
      this._alpha = Math.max(this._alpha, 0.3);
    });
    const end = (event) => {
      const node = this._nodes.get(id);
      if (node) {
        delete node.fx;
        delete node.fy;
      }
      if (drag && !drag.moved && event.type === "pointerup" && node) this._openMoreInfo(node);
      drag = null;
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
    el.addEventListener("pointerenter", () => el.classList.add("hover"));
    el.addEventListener("pointerleave", () => {
      el.classList.remove("hover");
      this._hideTooltip();
    });
  }

  _setupPanZoom() {
    let pan = null;
    this._svg.addEventListener("pointerdown", (event) => {
      pan = { x: event.clientX - this._view.x, y: event.clientY - this._view.y };
      this._svg.setPointerCapture(event.pointerId);
      this._svg.classList.add("panning");
    });
    this._svg.addEventListener("pointermove", (event) => {
      if (!pan) return;
      this._autoFit = false;
      this._view.x = event.clientX - pan.x;
      this._view.y = event.clientY - pan.y;
    });
    const end = () => {
      pan = null;
      this._svg.classList.remove("panning");
    };
    this._svg.addEventListener("pointerup", end);
    this._svg.addEventListener("pointercancel", end);
    this._svg.addEventListener("wheel", (event) => {
      event.preventDefault();
      this._autoFit = false;
      const rect = this._svg.getBoundingClientRect();
      const mx = event.clientX - rect.left;
      const my = event.clientY - rect.top;
      const k = Math.min(4, Math.max(0.3, this._view.k * (event.deltaY < 0 ? 1.1 : 1 / 1.1)));
      this._view.x = mx - ((mx - this._view.x) * k) / this._view.k;
      this._view.y = my - ((my - this._view.y) * k) / this._view.k;
      this._view.k = k;
    }, { passive: false });
    this._svg.addEventListener("dblclick", () => {
      this._autoFit = true;
    });
  }

  _showTooltip(node, event) {
    const d = node.data;
    let html;
    if (node.type === "root") {
      html = `<b>${escapeHtml(d.name)}</b>`;
    } else if (node.type === "ap") {
      html = `<b>${escapeHtml(d.name)}</b><br>
        <span class="muted">${escapeHtml(d.model ?? "")}${d.area ? " · " + escapeHtml(d.area) : ""}</span><br>
        ${d.online ? "Online" : this._t("offline")} · ${escapeHtml(d.clients ?? 0)} ${this._t("clients")}<br>
        <span class="muted">${escapeHtml(d.ip ?? "")}</span>`;
    } else {
      const ap = d.ap ? this._nodes.get(`ap:${d.ap}`)?.data?.name : null;
      html = `<b>${escapeHtml(d.name)}</b><br>
        ${d.connected
          ? `${escapeHtml(ap ?? "")}${d.rssi != null ? " · " + escapeHtml(d.rssi) + " dBm" : ""}<br>
             ${d.wired
               ? `${this._t("wired")}${d.port ? " · " + escapeHtml(d.port) : ""}`
               : `${d.band ? escapeHtml(d.band.replace("G", " GHz")) : ""}${d.channel != null ? ` · ${this._t("channel")} ` + escapeHtml(d.channel) : ""}`}<br>
             <span class="muted">${escapeHtml(d.ssid ?? "")}</span><br>`
          : `<span class="muted">${this._t("offline")}</span><br>`}
        <span class="muted">${escapeHtml(d.ip ?? "")} · ${escapeHtml(d.mac)}</span>`;
    }
    const tip = this._tooltip;
    tip.innerHTML = html;
    tip.style.display = "block";
    const rect = this.shadowRoot.querySelector(".wrap").getBoundingClientRect();
    let left = event.clientX - rect.left + 14;
    let top = event.clientY - rect.top + 14;
    if (left + tip.offsetWidth > rect.width) left -= tip.offsetWidth + 28;
    if (top + tip.offsetHeight > rect.height) top -= tip.offsetHeight + 28;
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  }

  _hideTooltip() {
    if (this._tooltip) this._tooltip.style.display = "none";
  }

  _openMoreInfo(node) {
    const entityId = node.data.entity_id;
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", {
      bubbles: true, composed: true, detail: { entityId },
    }));
  }
}

if (!customElements.get("gwn-network-card")) {
  customElements.define("gwn-network-card", GwnNetworkCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "gwn-network-card",
    name: "GWN Network Map",
    description: "Live map of GWN access points and their clients",
    preview: true,
    documentationURL: "https://github.com/digitalbirdo/ha_gwn_manager",
  });
  console.info(`%c GWN-NETWORK-CARD %c ${VERSION} `, "background:#039be5;color:#fff", "");
}
