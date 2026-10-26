/**
 * GWN Manager – client diagnosis panel.
 *
 * Opened by the cards when a client is tapped (window.gwnClientDetail.open). Shows the
 * connection, how often the client roams and drops out (and why), the signal over time,
 * the event timeline and a rule-based assessment of the likely problem. Lives in its own
 * element on document.body, so the cards' re-rendering doesn't disturb it.
 */
(() => {
  if (window.gwnClientDetail) return;

  const REFRESH_MS = 5000;
  const WINDOWS = [[1, "1 h"], [6, "6 h"], [24, "24 h"], [168, "7 d"]];
  const PALETTE = ["#38c6e8", "#a78bfa", "#3ee08f", "#f5c542", "#f472b6", "#60a5fa", "#fb923c"];
  const BAND_COLORS = { "2.4G": "#f5c542", "5G": "#38c6e8", "6G": "#a78bfa" };
  const WEAK = -75;

  const TEXT = {
    en: {
      title: "Connection diagnosis", close: "Close", device: "Device page", window: "Period",
      connection: "Connection", offline: "Not connected", ap: "Access point", band: "Band", ssid: "SSID",
      signal: "Signal", rate: "Link rate", mode: "Wi-Fi mode", ip: "IP", source: "Source", lastRoam: "Last roam",
      fast: "802.11r fast roaming", init: "full re-association", sources: { api: "API", snmp: "SNMP", syslog: "Syslog" },
      assessment: "Likely problems", none: "Nothing conspicuous in this period.",
      confidence: { high: "high", medium: "medium", low: "low" },
      stats: "Statistics", roams: "Roams", fastRoams: "fast (802.11r)", fullRoams: "full re-association", bandChanges: "Band changes",
      pingpong: "Ping-pong", drops: "Drop-outs", connects: "Connects", authFail: "Auth failures",
      steering: "Steering", noCandidate: "no target", steerOk: "success", clientLeft: "Left by itself",
      signalChart: "Signal", rateChart: "Link rate (transmit)", noSamples: "No signal samples in this period.",
      timeline: "Events", noEvents: "No events in this period.", more: "Show more", steerHide: "Hide steering",
      since: "Collected since {time} (kept in memory, lost on restart).",
      noSyslog: "Syslog is off: only the coarse changes seen by the API/SNMP are shown. Turn on syslog in the options for roaming details.",
      weakShare: "time at ≤ −75 dBm", min: "weakest",
      ev: {
        connected: "Connected", disconnected: "Disconnected", roam_fast: "802.11r fast roaming", roam_init: "Full re-association",
        roam_away: "Moved away (AP released it)", disassoc: "Client sent disassociation", auth_fail: "Authentication refused",
        assoc_fail: "Association refused", pingpong: "Ping-pong roam detected", roam_stats: "Roam quality problems",
        steer_req: "Steering requested", steer_resp: "Steering answer", roam_state: "Moved to another AP",
      },
      from: "from", to: "to", reason: "reason", status: "status", count: "×",
      drop: { client: "the client left by itself (sleep, Wi-Fi off, leaving range)", roamed: "released by the AP after a roam", weak_signal: "weak signal before the drop", unknown: "cause unknown" },
      reasons: {
        1: "unspecified", 2: "previous authentication no longer valid", 3: "client is leaving", 4: "inactivity", 5: "AP is overloaded",
        6: "frame from unauthenticated client", 7: "frame from unassociated client", 8: "client left the BSS", 15: "4-way handshake timed out",
        16: "group key handshake timed out", 23: "802.1X authentication failed", 34: "poor channel conditions", 201: "roamed away (steering/roaming)",
      },
      statuses: { 1: "unspecified failure", 12: "denied", 17: "AP can't handle more clients", 18: "rate mismatch", 53: "invalid PMKID", 77: "SAE: unsupported" },
      find: {
        sticky: ["Client sticks to its access point",
          "The signal is weak for a long time, yet the client doesn't move ({rssi_min} dBm at worst, {no_candidate} steering attempts without a target). Raise the minimum RSSI / roaming threshold of the SSID, check transmit power (too high keeps clients on a far AP) and whether a closer AP covers the spot."],
        steer_no_target: ["Steering finds no target",
          "{no_candidate} steering requests were answered with “no candidate” and none succeeded. Harmless if the signal is good; otherwise the other APs are too weak or on the wrong band at that place, or band steering rejects them: check coverage, transmit power and the RSSI thresholds of the neighbouring APs."],
        no_fast_roaming: ["No fast roaming (802.11r)",
          "{full} moves were full re-associations. Fast roaming isn't used: check that 802.11r is on for the SSID and that it matches the security mode (WPA2/WPA3 mixed mode has no FT-SAE; use WPA3-only or WPA2). Some clients never use 802.11r."],
        pingpong: ["Ping-pong between access points",
          "The client moved back to the previous AP within a minute {count} times. Cells overlap strongly or transmit power is too high; lower the power of the APs or raise the roaming threshold."],
        band_flapping: ["Frequent band changes at one AP",
          "{count} changes between bands at the same AP. Check band steering and the 6 GHz setting of the SSID; clients may flip between 5 and 6 GHz at the edge of coverage."],
        auth_failures: ["Connection attempts refused",
          "{count} authentication/association attempts were refused. Typical: wrong password, WPA3 not supported by the client, or the AP at its client limit."],
        drops_weak_signal: ["Drop-outs at weak signal",
          "{count} times the connection ended at ≤ −75 dBm and didn't come back at once. The client left the coverage or the AP is too far: improve coverage there."],
        drops_client: ["Client leaves by itself",
          "{count} disconnects came from the client. Often power saving (ESP, phones) – harmless unless the device then stops working."],
        drops_unknown: ["Drop-outs without a visible cause",
          "{count} disconnects with no reconnect within 30 s and no hint in the log. Check the device (power, firmware) and interference on its channel."],
        router_blind: ["This AP reports little",
          "The AP the client is on sends no usable syslog (e.g. the router). Roams to and from it only show up through SNMP/API samples."],
        need_debug: ["Few details available",
          "Set the syslog level of the APs to “debug” and turn on debug events in the options to see steering and roaming decisions."],
      },
    },
    de: {
      title: "Verbindungs-Diagnose", close: "Schließen", device: "Geräteseite", window: "Zeitraum",
      connection: "Verbindung", offline: "Nicht verbunden", ap: "Access Point", band: "Band", ssid: "SSID",
      signal: "Signal", rate: "Verbindungsrate", mode: "WLAN-Modus", ip: "IP", source: "Quelle", lastRoam: "Letzter Wechsel",
      fast: "802.11r Fast Roaming", init: "volle Neuanmeldung", sources: { api: "API", snmp: "SNMP", syslog: "Syslog" },
      assessment: "Wahrscheinliche Probleme", none: "In diesem Zeitraum nichts Auffälliges.",
      confidence: { high: "hoch", medium: "mittel", low: "niedrig" },
      stats: "Statistik", roams: "Wechsel", fastRoams: "schnell (802.11r)", fullRoams: "volle Neuanmeldung", bandChanges: "Bandwechsel",
      pingpong: "Ping-Pong", drops: "Abbrüche", connects: "Verbindungen", authFail: "Anmeldefehler",
      steering: "Steering", noCandidate: "kein Ziel", steerOk: "erfolgreich", clientLeft: "Selbst getrennt",
      signalChart: "Signal", rateChart: "Verbindungsrate (Senden)", noSamples: "Keine Signalwerte in diesem Zeitraum.",
      timeline: "Ereignisse", noEvents: "Keine Ereignisse in diesem Zeitraum.", more: "Mehr anzeigen", steerHide: "Steering ausblenden",
      since: "Gesammelt seit {time} (nur im Speicher, nach Neustart weg).",
      noSyslog: "Syslog ist aus: gezeigt werden nur die groben Änderungen aus API/SNMP. Für Roaming-Details Syslog in den Optionen einschalten.",
      weakShare: "Zeit bei ≤ −75 dBm", min: "schwächster Wert",
      ev: {
        connected: "Verbunden", disconnected: "Getrennt", roam_fast: "802.11r Fast Roaming", roam_init: "Volle Neuanmeldung",
        roam_away: "Weggewechselt (vom AP freigegeben)", disassoc: "Client hat sich abgemeldet", auth_fail: "Authentifizierung abgelehnt",
        assoc_fail: "Anmeldung abgelehnt", pingpong: "Ping-Pong-Wechsel erkannt", roam_stats: "Roaming-Qualitätsprobleme",
        steer_req: "Steering angefragt", steer_resp: "Steering-Antwort", roam_state: "Zu anderem AP gewechselt",
      },
      from: "von", to: "nach", reason: "Grund", status: "Status", count: "×",
      drop: { client: "der Client hat sich selbst getrennt (Standby, WLAN aus, außer Reichweite)", roamed: "vom AP nach einem Wechsel freigegeben", weak_signal: "schwaches Signal vor dem Abbruch", unknown: "Ursache unbekannt" },
      reasons: {
        1: "nicht näher angegeben", 2: "bisherige Authentifizierung nicht mehr gültig", 3: "Client geht weg", 4: "Inaktivität", 5: "AP überlastet",
        6: "Frame von nicht authentifiziertem Client", 7: "Frame von nicht angemeldetem Client", 8: "Client hat das BSS verlassen", 15: "4-Way-Handshake abgelaufen",
        16: "Gruppenschlüssel-Handshake abgelaufen", 23: "802.1X-Authentifizierung fehlgeschlagen", 34: "schlechte Kanalbedingungen", 201: "weggewechselt (Steering/Roaming)",
      },
      statuses: { 1: "nicht näher angegebener Fehler", 12: "abgelehnt", 17: "AP kann keine weiteren Clients aufnehmen", 18: "Ratenkonflikt", 53: "ungültige PMKID", 77: "SAE: nicht unterstützt" },
      find: {
        sticky: ["Client klebt an seinem Access Point",
          "Das Signal ist lange schwach, trotzdem wechselt der Client nicht (schlechtester Wert {rssi_min} dBm, {no_candidate} Steering-Versuche ohne Ziel). Minimum-RSSI/Roaming-Schwelle der SSID anheben, Sendeleistung prüfen (zu hoch hält Clients an einem fernen AP) und ob ein näherer AP die Stelle abdeckt."],
        steer_no_target: ["Steering findet kein Ziel",
          "{no_candidate} Steering-Anfragen wurden mit „kein Kandidat“ beantwortet, keine war erfolgreich. Harmlos bei gutem Signal; sonst sind die anderen APs an der Stelle zu schwach oder auf dem falschen Band, oder das Band-Steering lehnt sie ab: Abdeckung, Sendeleistung und RSSI-Schwellen der Nachbar-APs prüfen."],
        no_fast_roaming: ["Kein Fast Roaming (802.11r)",
          "{full} Wechsel waren volle Neuanmeldungen. Fast Roaming wird nicht genutzt: prüfen, ob 802.11r an der SSID aktiv ist und zum Sicherheitsmodus passt (WPA2/WPA3-Mischbetrieb hat kein FT-SAE; WPA3-only oder WPA2 verwenden). Manche Clients nutzen 802.11r nie."],
        pingpong: ["Ping-Pong zwischen Access Points",
          "Der Client wechselte {count}× innerhalb einer Minute zum vorigen AP zurück. Die Zellen überlappen stark oder die Sendeleistung ist zu hoch; Leistung der APs senken oder Roaming-Schwelle anheben."],
        band_flapping: ["Häufige Bandwechsel an einem AP",
          "{count} Wechsel zwischen den Bändern am selben AP. Band-Steering und 6-GHz-Einstellung der SSID prüfen; Clients springen am Rand der Reichweite gern zwischen 5 und 6 GHz."],
        auth_failures: ["Verbindungsversuche abgelehnt",
          "{count} Authentifizierungen/Anmeldungen wurden abgelehnt. Typisch: falsches Passwort, WPA3 vom Client nicht unterstützt oder der AP hat sein Client-Limit erreicht."],
        drops_weak_signal: ["Abbrüche bei schwachem Signal",
          "{count}× endete die Verbindung bei ≤ −75 dBm und kam nicht sofort zurück. Der Client verlässt die Reichweite oder der AP ist zu weit weg: Abdeckung dort verbessern."],
        drops_client: ["Client trennt sich selbst",
          "{count} Trennungen gingen vom Client aus. Oft Stromsparen (ESP, Handys) – harmlos, solange das Gerät danach funktioniert."],
        drops_unknown: ["Abbrüche ohne erkennbare Ursache",
          "{count} Trennungen ohne Wiederverbindung innerhalb von 30 s und ohne Hinweis im Log. Gerät (Strom, Firmware) und Störungen auf seinem Kanal prüfen."],
        router_blind: ["Dieser AP meldet wenig",
          "Der AP, an dem der Client hängt, sendet kein brauchbares Syslog (z. B. der Router). Wechsel von und zu ihm zeigen sich nur über SNMP/API-Messwerte."],
        need_debug: ["Wenig Details verfügbar",
          "Syslog-Stufe der APs auf „Debug“ stellen und Debug-Ereignisse in den Optionen einschalten, um Steering- und Roaming-Entscheidungen zu sehen."],
      },
    },
  };

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fill = (text, params) => text.replace(/\{(\w+)\}/g, (_, key) => (params?.[key] ?? "?"));

  const CSS = `
  :host { position: fixed; inset: 0; z-index: 9; display: flex; align-items: flex-start; justify-content: center; font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
  .scrim { position: absolute; inset: 0; background: rgba(0,0,0,.5); }
  .sheet { position: relative; box-sizing: border-box; width: min(760px, 100%); max-height: 100%; overflow: auto; margin-top: 3vh;
    background: var(--bg); color: var(--text); border-radius: 14px; padding: 16px 18px 20px; box-shadow: 0 10px 40px rgba(0,0,0,.45); }
  @media (max-width: 640px) { .sheet { margin: 0; border-radius: 0; height: 100%; } }
  .dark { --bg: #111a2b; --card: #17233a; --text: #e6edf7; --muted: #8fa1bd; --line: #2a3a58; --accent: #38c6e8; --ok: #3ee08f; --warn: #f5c542; --bad: #ff6b6b; }
  .light { --bg: #fff; --card: #f1f4f9; --text: #1b2433; --muted: #5d6b82; --line: #d5dce8; --accent: #0a7fa6; --ok: #1a9d5b; --warn: #b8860b; --bad: #d23a3a; }
  header { display: flex; gap: 10px; align-items: center; }
  header h2 { margin: 0; font-size: 18px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  header small { display: block; color: var(--muted); font-weight: 400; font-size: 12px; }
  button { font: inherit; color: var(--text); background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 4px 10px; cursor: pointer; }
  button.on { border-color: var(--accent); color: var(--accent); }
  .bar { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 10px 0; color: var(--muted); font-size: 13px; }
  section { margin-top: 16px; }
  h3 { margin: 0 0 6px; font-size: 13px; letter-spacing: .04em; text-transform: uppercase; color: var(--muted); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
  .tile { background: var(--card); border-radius: 10px; padding: 8px 10px; }
  .tile b { display: block; font-size: 18px; font-weight: 600; }
  .tile span { color: var(--muted); font-size: 12px; }
  .tile.warn b { color: var(--warn); } .tile.bad b { color: var(--bad); }
  .kv { display: grid; grid-template-columns: auto 1fr; gap: 3px 14px; font-size: 14px; }
  .kv dt { color: var(--muted); } .kv dd { margin: 0; }
  .finding { background: var(--card); border-left: 4px solid var(--warn); border-radius: 8px; padding: 8px 12px; margin-bottom: 8px; }
  .finding.high { border-color: var(--bad); } .finding.low { border-color: var(--muted); }
  .finding b { font-size: 14px; } .finding p { margin: 4px 0 0; font-size: 13px; line-height: 1.4; }
  .finding em { font-style: normal; color: var(--muted); font-size: 11px; margin-left: 6px; }
  .note { color: var(--muted); font-size: 13px; }
  svg { width: 100%; height: auto; display: block; background: var(--card); border-radius: 10px; }
  svg text { fill: var(--muted); font-size: 10px; }
  .legend { display: flex; gap: 12px; flex-wrap: wrap; font-size: 12px; color: var(--muted); margin-top: 4px; }
  .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
  ul.events { list-style: none; margin: 0; padding: 0; font-size: 13px; }
  ul.events li { display: grid; grid-template-columns: 62px 1fr; gap: 8px; padding: 4px 0; border-bottom: 1px solid var(--line); }
  ul.events time { color: var(--muted); font-variant-numeric: tabular-nums; }
  ul.events .sub { color: var(--muted); font-size: 12px; }
  ul.events li.ok b { color: var(--ok); } ul.events li.bad b { color: var(--bad); } ul.events li.warn b { color: var(--warn); }
  `;

  class GwnClientDetail extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._hours = 24;
      this._limit = 40;
      this._hideSteer = true;
    }

    open(opts) {
      this._opts = opts;
      this._data = null;
      this._error = null;
      this._keyHandler = (event) => event.key === "Escape" && this.close();
      document.addEventListener("keydown", this._keyHandler);
      this._render();
      this._refresh();
      this._timer = setInterval(() => this._refresh(), REFRESH_MS);
    }

    close() {
      clearInterval(this._timer);
      document.removeEventListener("keydown", this._keyHandler);
      this.remove();
      if (window.gwnClientDetail._el === this) window.gwnClientDetail._el = null;
    }

    get _lang() {
      return ((this._opts.hass?.language || "en").split("-")[0] in TEXT) ? (this._opts.hass.language).split("-")[0] : "en";
    }

    get _t() {
      return TEXT[this._lang];
    }

    async _refresh() {
      const { hass, mac, entryId } = this._opts;
      try {
        this._data = await hass.connection.sendMessagePromise({
          type: "gwn_manager/client_diagnostics", mac, hours: this._hours, ...(entryId ? { entry_id: entryId } : {}),
        });
        this._error = null;
      } catch (err) {
        this._error = err?.message || String(err);
      }
      this._render();
    }

    _apName(mac) {
      return (this._opts.aps || []).find((ap) => ap.mac === mac)?.name || mac || "?";
    }

    _time(iso, long = false) {
      const date = new Date(iso);
      const time = date.toLocaleTimeString(this._lang, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
      return long || this._hours > 24 ? `${date.toLocaleDateString(this._lang, { day: "2-digit", month: "2-digit" })} ${time}` : time;
    }

    _render() {
      const t = this._t;
      const o = this._opts;
      const dark = o.hass?.themes?.darkMode;
      const d = this._data;
      const scroll = this.shadowRoot.querySelector(".sheet")?.scrollTop ?? 0;
      this.shadowRoot.innerHTML = `
        <style>${CSS}</style>
        <div class="scrim"></div>
        <div class="sheet ${dark ? "dark" : "light"}">
          <header>
            <h2>${esc(o.name)}<small>${esc(t.title)} · ${esc(o.mac)}</small></h2>
            ${o.deviceId || o.entityId ? `<button data-device>${esc(t.device)}</button>` : ""}
            <button data-close>${esc(t.close)}</button>
          </header>
          <div class="bar">${esc(t.window)}:
            ${WINDOWS.map(([h, label]) => `<button data-hours="${h}" class="${h === this._hours ? "on" : ""}">${label}</button>`).join("")}
          </div>
          ${this._error ? `<p class="note">${esc(this._error)}</p>` : ""}
          ${d ? this._body(d) : `<p class="note">…</p>`}
        </div>`;
      const root = this.shadowRoot;
      root.querySelector(".sheet").scrollTop = scroll;
      root.querySelector(".scrim").addEventListener("click", () => this.close());
      root.querySelector("[data-close]").addEventListener("click", () => this.close());
      root.querySelector("[data-device]")?.addEventListener("click", () => this._openDevice());
      for (const button of root.querySelectorAll("[data-hours]")) {
        button.addEventListener("click", () => {
          this._hours = Number(button.dataset.hours);
          this._limit = 40;
          this._refresh();
        });
      }
      root.querySelector("[data-more]")?.addEventListener("click", () => {
        this._limit += 60;
        this._render();
      });
      root.querySelector("[data-steer]")?.addEventListener("click", () => {
        this._hideSteer = !this._hideSteer;
        this._render();
      });
    }

    _openDevice() {
      const { deviceId, entityId } = this._opts;
      this.close();
      if (deviceId) {
        history.pushState(null, "", `/config/devices/device/${deviceId}`);
        window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
      } else if (entityId) {
        window.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId } }));
      }
    }

    _body(d) {
      const t = this._t;
      return [
        d.source === "state" || !d.syslog ? `<p class="note">${esc(t.noSyslog)}</p>` : "",
        new Date(d.collecting_since) > new Date(d.since) ? `<p class="note">${esc(fill(t.since, { time: this._time(d.collecting_since, true) }))}</p>` : "",
        this._current(d),
        this._findings(d),
        this._stats(d),
        this._charts(d),
        this._timeline(d),
      ].join("");
    }

    _current(d) {
      const t = this._t;
      const c = d.current;
      const rows = [];
      const add = (label, value) => value != null && value !== "" && rows.push(`<dt>${esc(label)}</dt><dd>${value}</dd>`);
      if (!c.connected) add(t.connection, esc(t.offline));
      add(t.ap, esc(this._apName(c.ap)));
      add(t.band, esc([c.band, c.channel != null ? `Ch ${c.channel}` : null].filter(Boolean).join(" · ")));
      add(t.ssid, esc(c.ssid));
      add(t.signal, c.rssi != null ? `${esc(c.rssi)} dBm${c.snr != null ? ` · SNR ${esc(c.snr)} dB` : ""}` : null);
      add(t.rate, c.tx_rate != null ? `↑${esc(c.tx_rate)} ${c.rx_rate != null ? `↓${esc(c.rx_rate)} ` : ""}Mbit/s` : null);
      add(t.mode, esc(c.sta_mode));
      add(t.ip, esc(c.ip));
      add(t.lastRoam, c.last_roam ? esc(c.last_roam === "roam_fast" ? t.fast : t.init) : null);
      add(t.source, esc(t.sources[c.source] || c.source));
      return `<section><h3>${esc(t.connection)}</h3><dl class="kv">${rows.join("")}</dl></section>`;
    }

    _findings(d) {
      const t = this._t;
      const items = d.findings.map((f) => {
        const [title, text] = t.find[f.id] || [f.id, ""];
        return `<div class="finding ${esc(f.confidence)}"><b>${esc(title)}</b><em>${esc(t.confidence[f.confidence])}</em>
          <p>${esc(fill(text, f.params))}</p></div>`;
      });
      return `<section><h3>${esc(t.assessment)}</h3>${items.join("") || `<p class="note">${esc(t.none)}</p>`}</section>`;
    }

    _stats(d) {
      const t = this._t;
      const s = d.stats;
      const tile = (value, label, level = "") => `<div class="tile ${level}"><b>${esc(value)}</b><span>${esc(label)}</span></div>`;
      const tiles = [
        tile(s.roams, t.roams),
        tile(s.roams_fast, t.fastRoams),
        tile(s.roams_init, t.fullRoams, s.roams_init > s.roams_fast && s.roams_init >= 3 ? "warn" : ""),
        tile(s.band_changes, t.bandChanges, s.band_changes >= 6 ? "warn" : ""),
        tile(s.pingpong, t.pingpong, s.pingpong >= 2 ? "warn" : ""),
        tile(s.drops, t.drops, s.drops >= 3 ? "bad" : ""),
        tile(s.connects, t.connects),
        tile(s.client_disassoc, t.clientLeft),
        tile(s.auth_failures, t.authFail, s.auth_failures ? "bad" : ""),
      ];
      if (s.steer_requests) {
        tiles.push(tile(s.steer_requests, t.steering), tile(s.steer_no_candidate, `${t.steering}: ${t.noCandidate}`, s.steer_no_candidate >= 10 ? "warn" : ""),
          tile(s.steer_success, `${t.steering}: ${t.steerOk}`));
      }
      if (s.rssi_min != null) {
        tiles.push(tile(`${s.rssi_min} dBm`, t.min), tile(`${Math.round((s.weak_share || 0) * 100)} %`, t.weakShare, s.weak_share >= 0.3 ? "warn" : ""));
      }
      const drops = Object.entries(s.drop_causes || {}).map(([cause, n]) => `${n}× ${t.drop[cause] || cause}`);
      return `<section><h3>${esc(t.stats)}</h3><div class="grid">${tiles.join("")}</div>
        ${drops.length ? `<p class="note">${esc(t.drops)}: ${esc(drops.join("; "))}</p>` : ""}</section>`;
    }

    _charts(d) {
      const t = this._t;
      const samples = d.samples.filter((s) => s.rssi != null && s.rssi < 0);
      if (!samples.length) return `<section><h3>${esc(t.signalChart)}</h3><p class="note">${esc(t.noSamples)}</p></section>`;
      const W = 600, H = 150, L = 30, R = 6, T = 8, B = 16;
      const t0 = new Date(d.since).getTime(), t1 = new Date(d.now).getTime();
      const x = (iso) => L + ((new Date(iso).getTime() - t0) / (t1 - t0)) * (W - L - R);
      const y = (v, lo, hi, h = H, b = B) => T + ((hi - Math.min(Math.max(v, lo), hi)) / (hi - lo)) * (h - T - b);
      const aps = [...new Set(samples.map((s) => s.ap))];
      const color = (ap) => PALETTE[aps.indexOf(ap) % PALETTE.length];
      const gap = Math.max(180000, (t1 - t0) / 60);
      const line = (list, key, lo, hi, h = H, b = B, width = 1.6) => {
        let paths = "", prev = null, path = "";
        const flush = () => {
          if (path && !path.includes("L")) {
            const [px, py] = path.slice(1).split(",");
            paths += `<circle cx="${px}" cy="${py}" r="2.2" fill="${color(prev.ap)}"/>`;
          } else if (path) paths += `<path d="${path}" fill="none" stroke="${color(prev.ap)}" stroke-width="${width}"/>`;
          path = "";
        };
        for (const s of list) {
          if (s[key] == null) continue;
          const broken = prev && (prev.ap !== s.ap || new Date(s.t) - new Date(prev.t) > gap);
          if (broken) flush();
          path += `${path ? "L" : "M"}${x(s.t).toFixed(1)},${y(s[key], lo, hi, h, b).toFixed(1)}`;
          prev = s;
        }
        flush();
        return paths;
      };
      const grid = [-40, -60, -75, -90].map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v, -95, -30)}" y2="${y(v, -95, -30)}" stroke="${v === WEAK ? "#ff6b6b" : "currentColor"}" stroke-opacity="${v === WEAK ? .5 : .15}" stroke-dasharray="${v === WEAK ? "4 3" : ""}"/><text x="2" y="${y(v, -95, -30) + 3}">${v}</text>`).join("");
      const marks = d.moves.filter((m) => m.kind === "roam" || m.kind === "band").map((m) => {
        const fast = m.how === "fast";
        return `<path d="M${x(m.t).toFixed(1)},${H - B} v-${H - T - B}" stroke="${fast ? "#3ee08f" : m.kind === "band" ? "#8fa1bd" : "#fb923c"}" stroke-opacity=".5"/>`;
      }).join("");
      const drops = d.drops.map((m) => `<text x="${x(m.t).toFixed(1)}" y="${T + 8}" text-anchor="middle" style="fill:#ff6b6b;font-size:12px">×</text>`).join("");
      let strip = "";
      samples.forEach((s, i) => {
        const next = samples[i + 1];
        const end = next && new Date(next.t) - new Date(s.t) <= gap ? x(next.t) : x(s.t) + 1.5;
        strip += `<rect x="${x(s.t).toFixed(1)}" y="${H - B + 4}" width="${Math.max(end - x(s.t), 1.5).toFixed(1)}" height="6" fill="${BAND_COLORS[s.band] || "#555"}"/>`;
      });
      const rates = samples.filter((s) => s.rate != null);
      const maxRate = Math.max(100, ...rates.map((s) => s.rate));
      const signal = `<svg viewBox="0 0 ${W} ${H}" role="img">${grid}${marks}${line(samples, "rssi", -95, -30)}${drops}${strip}
        <text x="${L}" y="${H - 1}">${esc(this._time(d.since))}</text><text x="${W - R}" y="${H - 1}" text-anchor="end">${esc(this._time(d.now))}</text></svg>`;
      const rate = rates.length ? `<h3 style="margin-top:12px">${esc(t.rateChart)}</h3>
        <svg viewBox="0 0 ${W} 70" role="img"><text x="2" y="14">${maxRate}</text>${line(rates, "rate", 0, maxRate, 70, 4, 1.4)}</svg>` : "";
      const legend = `<div class="legend">${aps.map((ap) => `<span><i style="background:${color(ap)}"></i>${esc(this._apName(ap))}</span>`).join("")}
        ${Object.entries(BAND_COLORS).map(([b, c]) => `<span><i style="background:${c}"></i>${esc(b)}</span>`).join("")}
        <span><i style="background:#3ee08f"></i>802.11r</span><span><i style="background:#fb923c"></i>${esc(t.init)}</span></div>`;
      return `<section><h3>${esc(t.signalChart)}</h3>${signal}${legend}${rate}</section>`;
    }

    _describe(e) {
      const t = this._t;
      const parts = [];
      const where = [e.ap ? this._apName(e.ap) : null, e.band].filter(Boolean).join(" · ");
      if (where) parts.push(where);
      if (e.from_ap || e.from_band) {
        parts.push(`${t.from} ${[e.from_ap ? this._apName(e.from_ap) : null, e.from_band].filter(Boolean).join(" · ")}`);
      }
      if (e.reason_code != null) parts.push(`${t.reason} ${e.reason_code}: ${t.reasons[e.reason_code] || "?"}`);
      if (e.status_code != null) parts.push(`${t.status} ${e.status_code}: ${t.statuses[e.status_code] || "?"}`);
      if (e.result) parts.push(`${e.result}${e.detail ? ` (${e.detail})` : ""}`);
      else if (e.detail) parts.push(e.detail);
      if (e.snr != null) parts.push(`SNR ${e.snr}`);
      if (e.rssi) parts.push(`${e.rssi} dBm`);
      return parts.join(" — ");
    }

    _timeline(d) {
      const t = this._t;
      const level = { connected: "ok", roam_fast: "ok", disconnected: "bad", auth_fail: "bad", assoc_fail: "bad", pingpong: "warn", roam_stats: "warn", roam_init: "warn" };
      const all = d.events.filter((e) => !(this._hideSteer && (e.type === "steer_req" || e.type === "steer_resp")));
      const shown = all.slice().reverse().slice(0, this._limit);
      const hasSteer = d.events.some((e) => e.type === "steer_req" || e.type === "steer_resp");
      const rows = shown.map((e) => `<li class="${level[e.type] || ""}"><time>${esc(this._time(e.t))}</time>
        <span><b>${esc(t.ev[e.type] || e.type)}${e.count > 1 ? ` ${t.count}${e.count}` : ""}</b><div class="sub">${esc(this._describe(e))}</div></span></li>`);
      return `<section><h3>${esc(t.timeline)}</h3>
        ${hasSteer ? `<div class="bar"><button data-steer class="${this._hideSteer ? "on" : ""}">${esc(t.steerHide)}</button></div>` : ""}
        ${rows.length ? `<ul class="events">${rows.join("")}</ul>` : `<p class="note">${esc(t.noEvents)}</p>`}
        ${all.length > this._limit ? `<div class="bar"><button data-more>${esc(t.more)}</button></div>` : ""}</section>`;
    }
  }

  customElements.define("gwn-client-detail", GwnClientDetail);

  window.gwnClientDetail = {
    _el: null,
    /** opts: { hass, mac, name, aps: [{mac, name}], deviceId, entityId, entryId } */
    open(opts) {
      this._el?.close();
      this._el = document.createElement("gwn-client-detail");
      document.body.appendChild(this._el);
      this._el.open(opts);
    },
  };
})();
