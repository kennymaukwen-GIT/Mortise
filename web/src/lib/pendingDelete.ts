// Cancelling means "stop following and delete the job on the server". The server only deletes jobs that
// are not running, so a job cancelled mid-run is remembered here and deleted as soon as it has stopped
// (also after a reload, since the list is kept in localStorage).
import { ApiError, deleteJob, getJob } from "../api/client";

const KEY = "mortise.pendingDeletes";
const POLL_MS = 3000;
const GIVE_UP_MS = 30 * 60 * 1000;

interface Pending {
  jobId: string;
  since: number;
}

function load(): Pending[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "[]") as unknown;
    return Array.isArray(v) ? v.filter((p): p is Pending => !!p && typeof (p as Pending).jobId === "string") : [];
  } catch {
    return [];
  }
}

function save(list: Pending[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    /* ignore */
  }
}

const active = new Set<string>();

async function attempt(jobId: string): Promise<boolean> {
  try {
    await deleteJob(jobId);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return true;
    return false;
  }
  try {
    await getJob(jobId);
    return false; // still there: it was running
  } catch (e) {
    return e instanceof ApiError && e.status === 404;
  }
}

function track(p: Pending) {
  if (active.has(p.jobId)) return;
  active.add(p.jobId);
  const tick = async () => {
    const gone = await attempt(p.jobId);
    if (gone || Date.now() - p.since > GIVE_UP_MS) {
      active.delete(p.jobId);
      save(load().filter((x) => x.jobId !== p.jobId));
      return;
    }
    setTimeout(tick, POLL_MS);
  };
  void tick();
}

/** Delete now if possible, otherwise keep trying until the job has stopped. */
export async function deleteSoon(jobId: string): Promise<void> {
  if (await attempt(jobId)) return;
  const p = { jobId, since: Date.now() };
  save([...load().filter((x) => x.jobId !== jobId), p]);
  track(p);
}

/** Resume deletions left over from an earlier visit. */
export function resumePendingDeletes(): void {
  for (const p of load()) track(p);
}
