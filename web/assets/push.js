// Go-live notifications for this device.
//
// Shared by Options and the off-air chip on /watch and /home. A device signs
// up through its browser's own push service: this registers /sw.js, subscribes
// with the server's public key and hands the subscription to the server. The
// permission prompt only ever appears from turnOn, which is called straight
// from a tap, never on load.
//
//   await pushNotify.state()   -> "on" | "off" | "blocked" | "home-screen"
//                                  | "unsupported" | "unavailable"
//   pushNotify.turnOn()        -> promise of the new state; call it first
//                                  thing in a click handler. A rejection
//                                  with `readable` set has a message to show.
//   await pushNotify.turnOff() -> "off"
//   pushNotify.explain(state)  -> one plain line about that state

(function () {
  // iPhone and iPad offer push only to a site added to the Home Screen and
  // opened from there. iPadOS says it is a Mac; the touch screen gives it away.
  function isIOS() {
    return /iPhone|iPad|iPod/.test(navigator.userAgent || "") ||
      (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  }
  function standalone() {
    return navigator.standalone === true ||
      window.matchMedia("(display-mode: standalone)").matches;
  }
  function supported() {
    return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  }

  let keyInfo = null;
  async function serverKey() {
    if (!keyInfo) {
      const reply = await fetch("/api/push/key");
      if (!reply.ok) throw new Error("key");
      keyInfo = await reply.json();
    }
    return keyInfo;
  }

  function keyBytes(b64) {
    const text = b64.replace(/-/g, "+").replace(/_/g, "/");
    const raw = atob(text + "=".repeat((4 - (text.length % 4)) % 4));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  // A subscription made with an older server key cannot be used; a browser
  // that does not report its key is taken at its word.
  function sameKey(sub, key) {
    const had = sub.options && sub.options.applicationServerKey;
    if (!had) return true;
    const bytes = new Uint8Array(had);
    return bytes.length === key.length && bytes.every((v, i) => v === key[i]);
  }

  async function registration() {
    await navigator.serviceWorker.register("/sw.js");
    return navigator.serviceWorker.ready;
  }

  async function currentSubscription() {
    const reg = await navigator.serviceWorker.getRegistration("/");
    return reg ? reg.pushManager.getSubscription() : null;
  }

  async function state() {
    if (isIOS() && !standalone()) return "home-screen";
    if (!supported()) return "unsupported";
    let info;
    try { info = await serverKey(); } catch { return "unavailable"; }
    if (!info.ready) return "unavailable";
    if (Notification.permission === "denied") return "blocked";
    if (Notification.permission !== "granted") return "off";
    try {
      const sub = await currentSubscription();
      return sub && sameKey(sub, keyBytes(info.key)) ? "on" : "off";
    } catch {
      return "off";
    }
  }

  async function post(url, body) {
    const reply = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!reply.ok) {
      const data = await reply.json().catch(() => ({}));
      const error = new Error(data.error || "The server refused it.");
      error.readable = true;
      throw error;
    }
  }

  async function finishOn(asked) {
    const permission = await asked;
    if (permission === "denied") return "blocked";
    if (permission !== "granted") return "off";
    const info = await serverKey();
    if (!info.ready) return "unavailable";
    const key = keyBytes(info.key);
    const reg = await registration();
    let sub = await reg.pushManager.getSubscription();
    if (sub && !sameKey(sub, key)) {
      await sub.unsubscribe();
      sub = null;
    }
    if (!sub) {
      try {
        sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
      } catch (e) {
        // Brave ships with push turned off, so subscribing fails there until
        // the person switches it on.
        if (!navigator.brave) throw e;
        const error = new Error("Brave blocks this until you turn on \"Use Google services for push messaging\" in Brave's settings, under Privacy and security. Then try again.");
        error.readable = true;
        throw error;
      }
    }
    try {
      await post("/api/push/subscribe", sub.toJSON());
    } catch (e) {
      // Not kept by the server, so not kept here either: the switch must not
      // show on for a device that will never be told.
      await sub.unsubscribe().catch(() => {});
      throw e;
    }
    return "on";
  }

  // Nothing is awaited before the request: Safari and Firefox only show the
  // prompt from inside the tap itself.
  function turnOn() {
    let asked;
    try {
      asked = Notification.requestPermission();
    } catch (e) {
      return Promise.reject(e);
    }
    return finishOn(asked);
  }

  async function turnOff() {
    const sub = await currentSubscription();
    if (!sub) return "off";
    const endpoint = sub.endpoint;
    // The browser's side first: it is what decides whether a notice arrives.
    // A server row left behind is removed when its push service reports it gone.
    await sub.unsubscribe();
    try { await post("/api/push/unsubscribe", { endpoint }); } catch (e) { /* see above */ }
    return "off";
  }

  const LINES = {
    on: "On for this device.",
    off: "Off for this device.",
    blocked: "Notifications are blocked for this site. Allow them in your browser's site settings, then turn this on.",
    "home-screen": "On iPhone or iPad: tap Share, then Add to Home Screen. Open it from there, sign in, and turn this on.",
    unsupported: "This browser cannot show notifications.",
    unavailable: "Notifications are not set up on this server.",
  };

  window.pushNotify = {
    state,
    turnOn,
    turnOff,
    explain: (s) => LINES[s] || "",
  };
})();
