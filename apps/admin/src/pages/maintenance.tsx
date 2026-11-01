import { type FormEvent } from 'react';
import { write } from '../api';
import { ActionFeedback, useAction } from '../ui';

export function CleanupForm({ workspaceId }: { workspaceId?: string }) {
  const action = useAction();
  const expected = workspaceId || 'DELETE EVENTS';
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    action.mutate(async () => {
      await write(
        workspaceId ? `/workspaces/${workspaceId}/cleanup` : '/maintenance/events/cleanup',
        data,
      );
      form.reset();
    });
  }
  return (
    <form onSubmit={submit}>
      <p>
        {workspaceId
          ? 'Permanently delete this workspace’s reports, delivery receipts, participants, attempts, job and schedule history, and workspace events. Pause the workspace first. Schedules are paused; the connection and channel settings are retained.'
          : 'Permanently delete all event logs and audit history across all workspaces. A single cleanup audit record is retained; new events continue to be recorded.'}
      </p>
      {workspaceId && (
        <p>
          Deleting receipts also resets duplicate-report protection for the cleared periods.
          Messages already posted in Slack are not deleted. Resolve running jobs and uncertain
          deliveries first.
        </p>
      )}
      <label>
        Type {expected} to confirm
        <input
          name="confirmation"
          required
          autoComplete="off"
          pattern={workspaceId ? undefined : 'DELETE EVENTS'}
        />
      </label>
      <label>
        Current admin password
        <input name="password" type="password" required autoComplete="current-password" />
      </label>
      <button disabled={action.isPending} className="primary">
        Permanently clear {workspaceId ? 'workspace history' : 'events'}
      </button>
      <ActionFeedback action={action} />
    </form>
  );
}
