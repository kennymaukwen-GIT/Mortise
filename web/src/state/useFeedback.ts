// The feedback of the job on screen: what was already sent (ratings, reports), whether the job may be used to improve
// recognition, and sending more. Survives reloads because it is read back from the server.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api/client";
import type { FeedbackIn, FeedbackItem } from "../api/types";
import { latestRatings, reportedElements } from "./feedback";

export interface FeedbackState {
  /** Everything sent for this job, oldest first. */
  items: FeedbackItem[];
  /** The job was converted with "help improve recognition" on. null until known. */
  consented: boolean | null;
  /** Corrections sent for this job. */
  corrections: number;
  ratings: Record<number, number>;
  reported: Set<string>;
  /** Send one piece of feedback; resolves when the server has saved it. */
  send(fb: FeedbackIn): Promise<void>;
}

export function useFeedback(jobId: string | null, ready: boolean, version: number): FeedbackState {
  const [data, setData] = useState<{ job: string; items: FeedbackItem[]; consented: boolean | null; corrections: number } | null>(null);
  const jobRef = useRef(jobId);
  jobRef.current = jobId;

  const refresh = useCallback(async (id: string) => {
    try {
      const fb = await api.getFeedback(id);
      if (jobRef.current === id) setData({ job: id, items: Array.isArray(fb.items) ? fb.items : [], consented: !!fb.consented, corrections: fb.corrections ?? 0 });
    } catch {
      /* the counts are a nicety: the controls work without them */
    }
  }, []);

  useEffect(() => {
    if (!jobId || !ready) return;
    void refresh(jobId);
  }, [jobId, ready, version, refresh]);

  const send = useCallback(
    async (fb: FeedbackIn) => {
      const id = jobRef.current;
      if (!id) throw new Error("no job");
      const res = await api.postFeedback(id, fb);
      // Show it at once; the server's list (with its time stamp) replaces it a moment later.
      setData((d) => {
        const base = d && d.job === id ? d : { job: id, items: [], consented: null, corrections: 0 };
        return { ...base, items: [...base.items, { ...fb, id: res?.id ?? `local-${base.items.length}` }] };
      });
      void refresh(id);
    },
    [refresh],
  );

  const current = data && data.job === jobId ? data : null;
  const items = useMemo(() => current?.items ?? [], [current]);
  const ratings = useMemo(() => latestRatings(items), [items]);
  const reported = useMemo(() => reportedElements(items), [items]);
  return { items, consented: current?.consented ?? null, corrections: current?.corrections ?? 0, ratings, reported, send };
}
