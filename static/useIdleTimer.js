const DEFAULT_ACTIVITY_EVENTS = ["mousemove", "keydown", "click", "scroll"];

export function useIdleTimer({
  idleTimeoutMs,
  warningTimeoutMs,
  activityEvents = DEFAULT_ACTIVITY_EVENTS,
  onActivity,
  onWarning,
  onActive,
  onTimeout,
}) {
  let idleTimerId = null;
  let timeoutTimerId = null;
  let started = false;
  let warningVisible = false;
  let ended = false;

  function clearTimers() {
    window.clearTimeout(idleTimerId);
    window.clearTimeout(timeoutTimerId);
    idleTimerId = null;
    timeoutTimerId = null;
  }

  function scheduleTimers() {
    clearTimers();
    idleTimerId = window.setTimeout(() => {
      if (ended) {
        return;
      }
      warningVisible = true;
      if (typeof onWarning === "function") {
        onWarning();
      }
      timeoutTimerId = window.setTimeout(() => {
        ended = true;
        if (typeof onTimeout === "function") {
          onTimeout();
        }
      }, warningTimeoutMs);
    }, idleTimeoutMs);
  }

  function reset({ shouldNotifyActivity = true } = {}) {
    if (ended) {
      return;
    }
    const wasWarningVisible = warningVisible;
    warningVisible = false;
    scheduleTimers();
    if (wasWarningVisible && typeof onActive === "function") {
      onActive();
    }
    if (shouldNotifyActivity && typeof onActivity === "function") {
      onActivity();
    }
  }

  function handleLocalActivity() {
    reset({ shouldNotifyActivity: true });
  }

  function handleVisibilityChange() {
    if (document.visibilityState === "visible") {
      reset({ shouldNotifyActivity: true });
    }
  }

  return {
    start() {
      if (started) {
        return;
      }
      started = true;
      ended = false;
      warningVisible = false;
      activityEvents.forEach((eventName) => {
        window.addEventListener(eventName, handleLocalActivity, { passive: true });
      });
      document.addEventListener("visibilitychange", handleVisibilityChange);
      scheduleTimers();
    },
    stop() {
      if (!started) {
        return;
      }
      started = false;
      clearTimers();
      activityEvents.forEach((eventName) => {
        window.removeEventListener(eventName, handleLocalActivity, { passive: true });
      });
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    },
    reset,
    receiveExternalActivity() {
      reset({ shouldNotifyActivity: false });
    },
    isWarningVisible() {
      return warningVisible;
    },
  };
}
