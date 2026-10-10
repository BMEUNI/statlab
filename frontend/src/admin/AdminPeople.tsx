import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getAdminSettings, listAdminUsers, updateAdminUser } from '../api/client.ts';
import type { UserPatch } from '../api/types.ts';
import { errorText, formatTime } from '../ui/format.ts';
import BudgetMeter from '../ui/BudgetMeter.tsx';
import { inheritedText, initials } from './budget.ts';
import LimitField from './LimitField.tsx';

export default function AdminPeople({ meId }: { meId: string }) {
  const queryClient = useQueryClient();
  const users = useQuery({ queryKey: ['admin', 'users'], queryFn: ({ signal }) => listAdminUsers(signal) });
  const settings = useQuery({ queryKey: ['admin', 'settings'], queryFn: ({ signal }) => getAdminSettings(signal) });
  const update = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: UserPatch }) => updateAdminUser(id, patch),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin'] });
    },
  });
  const defaultMonthly = settings.data?.default_monthly_limit_usd;
  const placeholder = settings.data === undefined ? 'No limit' : inheritedText(defaultMonthly ?? null);

  return (
    <div>
      <p className="help">Empty limit fields use the default. Spend resets on the 1st of each month (UTC).</p>
      {update.isError && <p role="alert">{errorText(update.error)}</p>}
      {users.isPending && <p>Loading…</p>}
      {users.isError && <p role="alert">{errorText(users.error)}</p>}
      {users.isSuccess && (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">People</caption>
            <thead>
              <tr>
                <th>Person</th>
                <th>Role</th>
                <th>This month</th>
                <th>Monthly limit</th>
                <th>Projects</th>
                <th>Runs</th>
                <th>Last sign-in</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {users.data.map((user) => (
                <tr key={user.id}>
                  <td>
                    <div className="person">
                      <span className="avatar small" aria-hidden="true">{initials(user.name)}</span>
                      <span><strong>{user.name}</strong><small className="help">{user.email}</small></span>
                    </div>
                  </td>
                  <td>
                    {user.is_admin ? <span className="pill signal">Admin</span> : <span>Member</span>}
                    {user.disabled && <span className="pill fault">Disabled</span>}
                  </td>
                  <td><BudgetMeter compact meter={user.month} label={`${user.name} this month`} /></td>
                  <td>
                    <LimitField
                      label={`Monthly limit for ${user.name}`}
                      value={user.monthly_limit_usd}
                      placeholder={placeholder}
                      pending={update.isPending}
                      onSave={(value) => update.mutate({ id: user.id, patch: { monthly_limit_usd: value } })}
                    />
                  </td>
                  <td className="num">{user.projects}</td>
                  <td className="num">{user.runs}</td>
                  <td>{formatTime(user.last_login_at)}</td>
                  <td>
                    {user.id === meId ? (
                      <span className="help">You</span>
                    ) : (
                      <div className="row-actions">
                        <button type="button" disabled={update.isPending} onClick={() => update.mutate({ id: user.id, patch: { is_admin: !user.is_admin } })}>
                          {user.is_admin ? 'Remove admin' : 'Make admin'}
                        </button>
                        {user.disabled ? (
                          <button type="button" disabled={update.isPending} onClick={() => update.mutate({ id: user.id, patch: { disabled: false } })}>Enable</button>
                        ) : (
                          <button
                            type="button"
                            disabled={update.isPending}
                            onClick={() => {
                              if (window.confirm(`Disable ${user.name}? They are signed out and cannot sign in until you enable the account again.`)) {
                                update.mutate({ id: user.id, patch: { disabled: true } });
                              }
                            }}
                          >Disable</button>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
