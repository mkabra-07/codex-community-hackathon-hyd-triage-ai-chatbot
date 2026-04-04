import { useIdleTimer } from "./useIdleTimer.js";

export function createSessionLifecycle({
  userId,
  session,
  config,
  endpoints,
  chatSessionStorageKey,
  warningModal,
  confirmModal,
  statusBanner,
}) {
  const idleTimeoutMs = Number(config.idleTimeoutSeconds || 60) * 1000;
  const warningTimeoutMs = Number(config.warningTimeoutSeconds || 30) * 1000;
  const storagePrefix = `triage-session-${userId}`;
  const broadcastKey = `${storagePrefix}-event`;
  const noticeKey = "triage-session-notice";
  const activityKey = `${storagePrefix}-activity`;

  const warningCountdownEl = warningModal?.querySelector("[data-session-warning-countdown]");
  const warningStayButtonEl = warningModal?.querySelector("[data-session-warning-stay]");
  const confirmApproveButtonEl = confirmModal?.querySelector("[data-session-confirm-approve]");
  const confirmCancelButtonEl = confirmModal?.querySelector("[data-session-confirm-cancel]");
  const confirmCopyEl = confirmModal?.querySelector("[data-session-confirm-copy]");

  const channelName = `${storagePrefix}-channel`;
  const channel = "BroadcastChannel" in window ? new BroadcastChannel(channelName) : null;

  let countdownIntervalId = null;
  let lastBroadcastAt = 0;
  let lastActivitySyncAt = 0;
  let ended = false;
  let pendingReason = "manual_end";
  let endingPromise = null;

  function showBanner(message, tone = "info") {
    if (!statusBanner) {
      return;
    }
    statusBanner.textContent = message;
    statusBanner.dataset.tone = tone;
    statusBanner.hidden = false;
  }

  function hideBanner() {
    if (statusBanner) {
      statusBanner.hidden = true;
      statusBanner.textContent = "";
      delete statusBanner.dataset.tone;
    }
  }

  function hideModal(modal) {
    if (!modal) {
      return;
    }
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
  }

  function showModal(modal) {
    if (!modal) {
      return;
    }
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
  }

  function stopCountdown() {
    window.clearInterval(countdownIntervalId);
    countdownIntervalId = null;
  }

  function startWarningCountdown() {
    if (!warningCountdownEl) {
      return;
    }
    const startedAt = Date.now();
    const render = () => {
      const elapsedSeconds = Math.floor((Date.now() - startedAt) / 1000);
      const remainingSeconds = Math.max(Math.ceil(warningTimeoutMs / 1000) - elapsedSeconds, 0);
      warningCountdownEl.textContent = String(remainingSeconds);
    };
    render();
    stopCountdown();
    countdownIntervalId = window.setInterval(render, 1000);
  }

  function persistNotice(message) {
    window.localStorage.setItem(noticeKey, JSON.stringify({
      message,
      createdAt: Date.now(),
    }));
  }

  function redirectToLogin(message) {
    if (chatSessionStorageKey) {
      window.localStorage.removeItem(chatSessionStorageKey);
    }
    if (message) {
      persistNotice(message);
    }
    window.location.assign(endpoints.login);
  }

  function emitCrossTabEvent(payload) {
    const event = {
      sessionId: session.sessionId,
      userId,
      ...payload,
      createdAt: Date.now(),
    };
    if (channel) {
      channel.postMessage(event);
    }
    window.localStorage.setItem(broadcastKey, JSON.stringify(event));
  }

  async function syncActivity() {
    if (ended) {
      return;
    }
    const now = Date.now();
    if (now - lastActivitySyncAt < 15000) {
      return;
    }
    lastActivitySyncAt = now;

    try {
      const response = await window.fetch(endpoints.activity, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
      });

      if (response.status === 401 || response.status === 440) {
        const payload = await safeJson(response);
        handleRemoteSessionEnd(payload?.reason || "session_ended", payload?.error);
      }
    } catch (_error) {
      showBanner("We could not refresh your session activity. We will try again on your next action.", "warning");
    }
  }

  function registerUserActivity() {
    hideBanner();
    const now = Date.now();
    window.localStorage.setItem(activityKey, String(now));
    if (now - lastBroadcastAt >= 1000) {
      lastBroadcastAt = now;
      emitCrossTabEvent({ type: "activity" });
    }
    void syncActivity();
  }

  function handleRemoteSessionEnd(reason, message) {
    if (ended) {
      return;
    }
    ended = true;
    idleTimer.stop();
    stopCountdown();
    hideModal(warningModal);
    hideModal(confirmModal);
    if (chatSessionStorageKey) {
      window.localStorage.removeItem(chatSessionStorageKey);
    }
    redirectToLogin(message || defaultMessageForReason(reason));
  }

  async function safeJson(response) {
    try {
      return await response.json();
    } catch (_error) {
      return null;
    }
  }

  async function terminateSession(reason, options = {}) {
    if (endingPromise) {
      return endingPromise;
    }

    const shouldRedirect = options.redirect !== false;
    const failureMessage = options.failureMessage || "We could not end the session. Please try again.";
    const successMessage = options.successMessage || defaultMessageForReason(reason);
    ended = true;
    idleTimer.stop();
    stopCountdown();
    hideModal(warningModal);
    hideModal(confirmModal);
    emitCrossTabEvent({ type: "session-ended", reason, message: successMessage });

    endingPromise = (async () => {
      try {
        const response = await window.fetch(endpoints.end, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ reason }),
        });
        const payload = await safeJson(response);

        if (!response.ok && response.status !== 401 && response.status !== 440) {
          ended = false;
          endingPromise = null;
          idleTimer.start();
          showBanner(payload?.error || failureMessage, "error");
          return { ok: false, payload };
        }

        if (shouldRedirect) {
          redirectToLogin(payload?.message || successMessage);
        }
        return { ok: true, payload };
      } catch (_error) {
        ended = false;
        endingPromise = null;
        idleTimer.start();
        showBanner(failureMessage, "error");
        return { ok: false, payload: null };
      }
    })();

    return endingPromise;
  }

  function handleCrossTabMessage(payload) {
    if (!payload || payload.sessionId !== session.sessionId) {
      return;
    }
    if (payload.type === "activity") {
      idleTimer.receiveExternalActivity();
      hideModal(warningModal);
      stopCountdown();
      return;
    }
    if (payload.type === "session-ended") {
      handleRemoteSessionEnd(payload.reason, payload.message);
    }
  }

  const idleTimer = useIdleTimer({
    idleTimeoutMs,
    warningTimeoutMs,
    onActivity: registerUserActivity,
    onWarning() {
      showModal(warningModal);
      startWarningCountdown();
    },
    onActive() {
      hideModal(warningModal);
      stopCountdown();
    },
    onTimeout() {
      void terminateSession("idle_timeout", {
        successMessage: "Session expired due to inactivity.",
        failureMessage: "Your session timed out, but we could not reach the server to finalize it. Please sign in again.",
      });
    },
  });

  function defaultMessageForReason(reason) {
    if (reason === "logout") {
      return "You have been logged out.";
    }
    if (reason === "manual_end") {
      return "Your session has been ended.";
    }
    if (reason === "idle_timeout") {
      return "Session expired due to inactivity.";
    }
    return "Your session is no longer active.";
  }

  return {
    start() {
      idleTimer.start();
      warningStayButtonEl?.addEventListener("click", () => idleTimer.reset());
      confirmCancelButtonEl?.addEventListener("click", () => hideModal(confirmModal));
      confirmApproveButtonEl?.addEventListener("click", () => {
        void terminateSession(pendingReason, {
          successMessage: pendingReason === "logout" ? "You have been logged out." : "Your session has been ended.",
        });
      });
      window.addEventListener("storage", (event) => {
        if (event.key === broadcastKey && event.newValue) {
          handleCrossTabMessage(JSON.parse(event.newValue));
        }
        if (event.key === activityKey && event.newValue) {
          idleTimer.receiveExternalActivity();
          hideModal(warningModal);
          stopCountdown();
        }
      });
      if (channel) {
        channel.addEventListener("message", (event) => handleCrossTabMessage(event.data));
      }
      void syncActivity();
    },
    confirmEndSession(reason = "manual_end") {
      pendingReason = reason;
      if (confirmCopyEl) {
        confirmCopyEl.textContent = reason === "logout"
          ? "Are you sure you want to log out?"
          : "Are you sure you want to end this session?";
      }
      showModal(confirmModal);
    },
    async fetchJson(url, options = {}) {
      const response = await window.fetch(url, {
        credentials: "same-origin",
        ...options,
      });
      const payload = await safeJson(response);
      if (response.status === 401 || response.status === 440 || payload?.sessionEnded) {
        handleRemoteSessionEnd(payload?.reason || "session_ended", payload?.error);
        throw new Error(payload?.error || "Your session is no longer active.");
      }
      return { response, payload };
    },
    handleRemoteSessionEnd,
  };
}
