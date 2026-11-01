import * as Dialog from '@radix-ui/react-dialog';
import { X, AlertCircle, LoaderCircle } from 'lucide-react';
import { createContext, useContext, useState, type ReactNode } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

export const Notices = createContext<(message: string) => void>(() => {});
export function useAction() {
  const queryClient = useQueryClient();
  const notify = useContext(Notices);
  return useMutation({
    mutationFn: (run: () => Promise<unknown>) => run(),
    onSuccess: () => {
      queryClient.invalidateQueries();
      notify('Changes saved.');
    },
    onError: (error) => notify(error.message),
  });
}
export function Badge({ children, good = false }: { children: ReactNode; good?: boolean }) {
  return <span className={'badge ' + (good ? 'good' : '')}>{children}</span>;
}
export function Empty({
  title = 'Nothing here yet.',
  children,
}: {
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">W</div>
      <h3>{title}</h3>
      <p>{children || 'Data will appear here as your workspace gets going.'}</p>
    </div>
  );
}
export function ErrorBox({ error }: { error: unknown }) {
  return (
    <div role="alert" className="error">
      <AlertCircle size={16} />
      {error instanceof Error ? error.message : 'Something went wrong. Please try again.'}
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading">
      <LoaderCircle className="spin" size={20} /> Loading…
    </div>
  );
}
export function ActionFeedback({
  action,
}: {
  action: { isSuccess: boolean; isError: boolean; error: Error | null };
}) {
  if (action.isError) return <ErrorBox error={action.error} />;
  return action.isSuccess ? (
    <p role="status" className="subtle">
      Changes saved. You can close this form.
    </p>
  ) : null;
}
export function Modal({
  title,
  description,
  children,
  trigger,
}: {
  title: string;
  description: string;
  children: ReactNode;
  trigger: ReactNode;
}) {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>{trigger}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content className="modal-content">
          <Dialog.Title>{title}</Dialog.Title>
          <Dialog.Description className="subtle">{description}</Dialog.Description>
          <Dialog.Close className="modal-close" aria-label="Close">
            <X size={18} />
          </Dialog.Close>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
export function Confirm({
  label,
  description,
  onConfirm,
  disabled,
}: {
  label: string;
  description: string;
  onConfirm: () => Promise<unknown>;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const action = useAction();
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button disabled={disabled}>{label}</button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content className="modal-content">
          <Dialog.Title>{label}</Dialog.Title>
          <Dialog.Description>{description}</Dialog.Description>
          <ActionFeedback action={action} />
          <div className="row end">
            <Dialog.Close asChild>
              <button>Cancel</button>
            </Dialog.Close>
            <button
              className="primary"
              disabled={action.isPending}
              onClick={() => action.mutate(onConfirm, { onSuccess: () => setOpen(false) })}
            >
              {action.isPending ? 'Working…' : 'Confirm'}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
export function dateTime(value: string | null, timezone?: string) {
  return value
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: 'medium',
        timeStyle: 'short',
        timeZone: timezone,
      }).format(new Date(value))
    : '—';
}
