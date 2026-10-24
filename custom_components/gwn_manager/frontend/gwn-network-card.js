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
 *   show_labels: false       # always show client names (otherwise on hover)
 *   animate: true            # gentle floating of the bubbles
 *   entry_id: ...            # optional, only one GWN Manager entry
 */

const VERSION = "0.4.0";

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

function linkLength(rssi) {
  // Stronger signal = shorter link: -30 dBm -> 45 px, -90 dBm -> 165 px
  if (rssi === null || rssi === undefined) return 120;
  return 45 + Math.min(Math.max(-rssi - 30, 0), 60) * 2;
}

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
    this._alpha = 1;
    this._view = { x: 0, y: 0, k: 1 };
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
      show_labels: false,
      animate: true,
      ...config,
    };
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first && this.isConnected) this._subscribe();
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
      for (const ap of net.aps) {
        const node = upsert(`ap:${ap.mac}`, "ap", ap, root);
        links.push({ source: root, target: node, length: 150, strength: 1 });
      }
      for (const client of net.clients) {
        if (!client.connected && !this._config.show_offline) continue;
        const apNode = client.connected && client.ap ? this._nodes.get(`ap:${client.ap}`) : null;
        const parent = apNode || root;
        const node = upsert(`cl:${client.mac}`, "client", client, parent);
        links.push({
          source: parent,
          target: node,
          length: apNode ? linkLength(client.rssi) : 230,
          strength: apNode ? 1 : 0.3,
          rssi: client.rssi,
          offline: !client.connected,
        });
      }
    }

    for (const id of [...this._nodes.keys()]) {
      if (!seen.has(id)) {
        this._nodes.delete(id);
        this._alpha = Math.max(this._alpha, 0.5);
      }
    }
    this._links = links;
    this._syncElements();
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
        let f = (REPULSION * CHARGE[a.type] * CHARGE[b.type]) / d2;
        const minDist = RADIUS[a.type] + RADIUS[b.type] + 4;
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

    for (const n of nodes) {
      n.vx += (cx - n.x) * GRAVITY * alpha * (n.type === "root" ? 5 : 1);
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
        .wrap { position: relative; height: ${Number(this._config.height)}px; touch-action: none; }
        svg { width: 100%; height: 100%; display: block; cursor: grab; user-select: none; }
        svg.panning { cursor: grabbing; }
        .link { stroke-linecap: round; }
        .link.offline { stroke-dasharray: 3 4; opacity: .35; }
        .node { cursor: pointer; }
        .node circle { stroke: var(--card-background-color, #fff); stroke-width: 2; transition: r .3s; }
        .node.root circle { fill: var(--primary-color); }
        .node.ap circle { fill: var(--info-color, #039be5); }
        .node.ap.offline circle { fill: var(--error-color, #db4437); }
        .node.client.offline circle { opacity: .45; }
        .node text {
          font: 11px var(--paper-font-body1_-_font-family, sans-serif);
          fill: var(--primary-text-color); paint-order: stroke;
          stroke: var(--card-background-color, #fff); stroke-width: 3px;
          pointer-events: none;
        }
        .node.ap text, .node.root text { font-weight: 600; font-size: 12px; }
        .node.client text { display: ${this._config.show_labels ? "block" : "none"}; }
        .node.client:hover text, .node.client.hover text { display: block; }
        .node .count { font-size: 10px; fill: #fff; stroke: none; font-weight: 600; }
        .tooltip {
          position: absolute; pointer-events: none; display: none; z-index: 1;
          background: var(--card-background-color, #fff); color: var(--primary-text-color);
          border: 1px solid var(--divider-color, #ddd); border-radius: 8px;
          box-shadow: 0 2px 8px rgba(0,0,0,.15); padding: 8px 10px;
          font-size: 12px; line-height: 1.5; max-width: 240px;
        }
        .tooltip b { font-size: 13px; }
        .tooltip .muted { color: var(--secondary-text-color); }
        .legend {
          position: absolute; left: 12px; bottom: 8px; display: flex; gap: 10px;
          font-size: 11px; color: var(--secondary-text-color); pointer-events: none;
        }
        .legend span::before {
          content: ""; display: inline-block; width: 9px; height: 9px; border-radius: 50%;
          margin-right: 4px; background: var(--c); vertical-align: -1px;
        }
        .error { padding: 16px; color: var(--error-color, #db4437); }
      </style>
      <ha-card header="${escapeHtml(this._config.title ?? "")}">
        <div class="wrap">
          <svg>
            <g class="viewport">
              <g class="links"></g>
              <g class="nodes"></g>
            </g>
          </svg>
          <div class="tooltip"></div>
          <div class="legend">
            <span style="--c: var(--success-color, #43a047)">≥ −60 dBm</span>
            <span style="--c: #9ccc65">≥ −70</span>
            <span style="--c: var(--warning-color, #ffa600)">≥ −80</span>
            <span style="--c: var(--error-color, #db4437)">&lt; −80</span>
          </div>
        </div>
        <div class="error" hidden></div>
      </ha-card>`;
    if (!this._config.title) this.shadowRoot.querySelector("ha-card").removeAttribute("header");
    this._svg = this.shadowRoot.querySelector("svg");
    this._viewport = this.shadowRoot.querySelector(".viewport");
    this._linkLayer = this.shadowRoot.querySelector(".links");
    this._nodeLayer = this.shadowRoot.querySelector(".nodes");
    this._tooltip = this.shadowRoot.querySelector(".tooltip");
    this._elements = new Map();
    this._linkElements = [];
    this._setupPanZoom();
    this._resize();
    if (this._nodes.size) this._syncElements();
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
        el.appendChild(document.createElementNS(SVG_NS, "circle"));
        const label = document.createElementNS(SVG_NS, "text");
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
      const circle = el.firstChild;
      const label = el.childNodes[1];
      let r = RADIUS[node.type];
      if (node.type === "ap") {
        const n = this._links.filter((l) => l.source === node).length;
        r += Math.min(Math.sqrt(n) * 2, 10);
        el.childNodes[2].textContent = n || "";
      }
      if (node.type === "client") circle.setAttribute("fill", rssiColor(d.rssi));
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
      line.setAttribute("class", `link${link.offline ? " offline" : ""}`);
      line.setAttribute("stroke", isClient ? rssiColor(link.rssi) : "var(--divider-color, #bbb)");
      line.setAttribute("stroke-width", isClient ? 1.5 : 3);
      line.setAttribute("stroke-opacity", isClient ? 0.6 : 0.9);
    });
  }

  _draw() {
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
    const { x, y, k } = this._view;
    this._viewport.setAttribute("transform", `translate(${x},${y}) scale(${k})`);
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
      const rect = this._svg.getBoundingClientRect();
      const mx = event.clientX - rect.left;
      const my = event.clientY - rect.top;
      const k = Math.min(4, Math.max(0.3, this._view.k * (event.deltaY < 0 ? 1.1 : 1 / 1.1)));
      this._view.x = mx - ((mx - this._view.x) * k) / this._view.k;
      this._view.y = my - ((my - this._view.y) * k) / this._view.k;
      this._view.k = k;
    }, { passive: false });
    this._svg.addEventListener("dblclick", () => {
      this._view = { x: 0, y: 0, k: 1 };
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
        ${d.online ? "Online" : "Offline"} · ${escapeHtml(d.clients ?? 0)} Clients<br>
        <span class="muted">${escapeHtml(d.ip ?? "")}</span>`;
    } else {
      const ap = d.ap ? this._nodes.get(`ap:${d.ap}`)?.data?.name : null;
      html = `<b>${escapeHtml(d.name)}</b><br>
        ${d.connected
          ? `${escapeHtml(ap ?? "")}${d.band ? " · " + escapeHtml(d.band) : ""}${d.rssi != null ? " · " + escapeHtml(d.rssi) + " dBm" : ""}<br>
             <span class="muted">${escapeHtml(d.ssid ?? "")}</span><br>`
          : `<span class="muted">Offline</span><br>`}
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
