import { useId, useState, type FormEvent } from 'react';
import { useMutation } from '@tanstack/react-query';
import { register, signIn } from '../api/client.ts';
import type { AuthConfig } from '../api/types.ts';
import { errorText } from '../ui/format.ts';

type Mode = 'signin' | 'register';

export default function AuthPage({ config, onSignedIn }: { config: AuthConfig; onSignedIn: () => void }) {
  const canRegister = config.signup_open || !config.has_users;
  const [mode, setMode] = useState<Mode>(config.has_users ? 'signin' : 'register');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const nameId = useId();
  const emailId = useId();
  const passwordId = useId();
  const isRegister = mode === 'register';

  const mutation = useMutation({
    mutationFn: () => (isRegister
      ? register(name.trim(), email.trim(), password)
      : signIn(email.trim(), password)),
    onSuccess: () => onSignedIn(),
  });

  function choose(next: Mode) {
    setMode(next);
    mutation.reset();
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    mutation.mutate();
  }

  const pending = mutation.isPending;
  const submitLabel = isRegister
    ? (pending ? 'Creating account…' : 'Create account')
    : (pending ? 'Signing in…' : 'Sign in');

  return (
    <div className="auth">
      <section className="auth-brand">
        <span className="brand-mark large" aria-hidden="true" />
        <p className="auth-name">Statlab</p>
        <p className="auth-line">Upload a table. An agent cleans it, fits several models and scores the best one once on held-out data. You approve the key decisions.</p>
      </section>
      <main className="auth-panel">
        <div className="auth-card">
          {canRegister && config.has_users && (
            <div className="tabs" role="tablist">
              <button type="button" role="tab" aria-selected={mode === 'signin'} onClick={() => choose('signin')}>Sign in</button>
              <button type="button" role="tab" aria-selected={mode === 'register'} onClick={() => choose('register')}>Create account</button>
            </div>
          )}
          <h1>{isRegister ? 'Create your account' : 'Sign in'}</h1>
          {!config.has_users && <p className="help">No accounts yet. The first account becomes the admin.</p>}
          <form onSubmit={submit}>
            {isRegister && (
              <div className="field">
                <label htmlFor={nameId}>Name</label>
                <input id={nameId} type="text" autoComplete="name" required maxLength={100} value={name} onChange={(event) => setName(event.target.value)} />
              </div>
            )}
            <div className="field">
              <label htmlFor={emailId}>Email</label>
              <input id={emailId} type="email" autoComplete="email" required maxLength={254} value={email} onChange={(event) => setEmail(event.target.value)} />
            </div>
            <div className="field">
              <label htmlFor={passwordId}>Password</label>
              <input
                id={passwordId}
                type="password"
                required
                autoComplete={isRegister ? 'new-password' : 'current-password'}
                minLength={isRegister ? 10 : undefined}
                maxLength={isRegister ? 128 : undefined}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
              {isRegister && <p className="help">At least 10 characters.</p>}
            </div>
            {mutation.isError && <p role="alert">{errorText(mutation.error)}</p>}
            <button className="primary auth-submit" type="submit" disabled={pending}>{submitLabel}</button>
          </form>
          {!canRegister && <p className="help">Sign-up is closed. Ask an admin for an account.</p>}
        </div>
      </main>
    </div>
  );
}
