import { useEffect, type RefObject } from "react";

import { api, type JellyfinSyncStatus } from "../lib/api";

export function useJellyfinSyncPolling({
  active,
  trackedJobId,
  onStatus,
  onCompleted,
  onCanceled,
  onFailed,
}: {
  active: boolean;
  trackedJobId: RefObject<number | null>;
  onStatus: (status: JellyfinSyncStatus) => void;
  onCompleted: (status: JellyfinSyncStatus) => Promise<void> | void;
  onCanceled: () => void;
  onFailed: (status: JellyfinSyncStatus) => void;
}) {
  useEffect(() => {
    if (!active) return;
    let mounted = true;
    let completionHandled = false;
    const refresh = async () => {
      try {
        const nextStatus = await api.jellyfinSyncStatus();
        if (!mounted) return;
        onStatus(nextStatus);
        if (
          completionHandled
          || trackedJobId.current === null
          || nextStatus.sync_job_id !== trackedJobId.current
          || nextStatus.sync_job_active
        ) return;
        completionHandled = true;
        trackedJobId.current = null;
        if (nextStatus.sync_job_status === "completed") {
          await onCompleted(nextStatus);
        } else if (nextStatus.sync_job_status === "canceled") {
          onCanceled();
        } else if (nextStatus.sync_job_status === "failed") {
          onFailed(nextStatus);
        }
      } catch {
        // Preserve the last known state; a later polling attempt may recover.
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 750);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, [active, onCanceled, onCompleted, onFailed, onStatus, trackedJobId]);
}
