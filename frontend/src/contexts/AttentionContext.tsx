import { createContext, useContext, useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "attention-notifications";

/** Permission as the browser reports it, plus an "unsupported" sentinel. */
export type AttentionPermission = NotificationPermission | "unsupported";

interface AttentionContextType {
  /** User's global on/off preference (persisted). */
  enabled: boolean;
  /** Current browser notification permission. */
  permission: AttentionPermission;
  /** Flip the global preference; requests permission when turning on. */
  toggle: () => void;
}

const AttentionContext = createContext<AttentionContextType | undefined>(undefined);

function readPermission(): AttentionPermission {
  if (typeof Notification === "undefined") return "unsupported";
  return Notification.permission;
}

export function AttentionProvider({ children }: { children: React.ReactNode }) {
  const [enabled, setEnabled] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return localStorage.getItem(STORAGE_KEY) === "true";
  });
  const [permission, setPermission] = useState<AttentionPermission>(readPermission);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, String(enabled));
  }, [enabled]);

  const toggle = useCallback(() => {
    setEnabled((prev) => {
      const next = !prev;
      // Ask for permission the first time the user turns it on.
      if (next && typeof Notification !== "undefined" && Notification.permission === "default") {
        Notification.requestPermission().then((result) => setPermission(result));
      }
      return next;
    });
  }, []);

  return (
    <AttentionContext.Provider value={{ enabled, permission, toggle }}>
      {children}
    </AttentionContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAttention() {
  const context = useContext(AttentionContext);
  if (!context) {
    throw new Error("useAttention must be used within an AttentionProvider");
  }
  return context;
}
