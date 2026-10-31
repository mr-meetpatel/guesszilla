import { useState, type FormEvent } from 'react';
import { ArrowUpRight, ShieldCheck } from 'lucide-react';
import { api, type User } from './api';

export function Login({ onSuccess }: { onSuccess: (user: User) => void }) {
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    setPending(true);
    const form = new FormData(event.currentTarget);
    try {
      onSuccess(
        await api<User>(
          '/auth/login',
          {
            method: 'POST',
            body: JSON.stringify({
              username: form.get('username'),
              password: form.get('password'),
            }),
          },
          false,
        ),
      );
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Sign in failed');
    } finally {
      setPending(false);
    }
  }
  return (
    <div className="login-shell">
      <section className="login-story">
        <div className="brand">
          <span className="brand-mark">W</span> Wordle Control
        </div>
        <div>
          <p className="eyebrow">A LITTLE COMPETITION. A LOT OF CONSISTENCY.</p>
          <h1>
            Good habits.
            <br />
            Great company.
          </h1>
          <p>
            Your workspaces, players, and weekly rituals.
            <br />
            One place to keep everything running.
          </p>
          <div className="wordle-grid">
            {'WORLDEVOLVESTATS'.split('').map((letter, i) => (
              <span key={i} className={i % 4 === 0 ? 'yellow' : i > 9 ? 'muted' : ''}>
                {letter}
              </span>
            ))}
          </div>
        </div>
        <span className="login-foot">Built for the people behind the leaderboard.</span>
      </section>
      <section className="login-form">
        <div>
          <ShieldCheck size={30} />
          <p className="eyebrow">SUPER-ADMIN ACCESS</p>
          <h2>Welcome back.</h2>
          <p className="subtle">Sign in to your control room.</p>
          <form onSubmit={submit}>
            <label>
              Username
              <input name="username" autoComplete="username" required maxLength={80} />
            </label>
            <label>
              Password
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
                maxLength={256}
              />
            </label>
            {error && (
              <div role="alert" className="error">
                {error}
              </div>
            )}
            <button className="primary" disabled={pending}>
              {pending ? 'Signing in…' : 'Sign in'}
              <ArrowUpRight size={17} />
            </button>
          </form>
          <p className="tiny">
            Access is restricted to authorized administrators.
            <br />
            Sessions and administrative actions are audited.
          </p>
        </div>
      </section>
    </div>
  );
}
