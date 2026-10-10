import { useId, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { UserPlus } from 'lucide-react';
import { addMember, listMembers, removeMember } from '../api/client.ts';
import type { AuthUser } from '../api/types.ts';
import type { Route } from '../routes.ts';
import { initials } from '../admin/budget.ts';
import { errorText } from '../ui/format.ts';

export default function MembersPanel({ projectId, me, navigate }: {
  projectId: string;
  me: AuthUser;
  navigate: (route: Route) => void;
}) {
  const members = useQuery({ queryKey: ['members', projectId], queryFn: ({ signal }) => listMembers(projectId, signal) });
  const queryClient = useQueryClient();
  const [email, setEmail] = useState('');
  const headingId = useId();
  const emailId = useId();
  const canManage = me.is_admin || (members.data ?? []).some((member) => member.user_id === me.id && member.role === 'owner');

  const add = useMutation({
    mutationFn: (value: string) => addMember(projectId, value),
    onSuccess: async () => {
      setEmail('');
      await queryClient.invalidateQueries({ queryKey: ['members', projectId] });
    },
  });

  const remove = useMutation({
    mutationFn: (userId: string) => removeMember(projectId, userId),
    onSuccess: async (_data, userId) => {
      if (userId === me.id) {
        navigate({ name: 'projects' });
        return;
      }
      await queryClient.invalidateQueries({ queryKey: ['members', projectId] });
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    add.mutate(email.trim());
  }

  function leave() {
    if (window.confirm('Leave this project? You lose access until the owner adds you again.')) remove.mutate(me.id);
  }

  return (
    <section className="panel members" aria-labelledby={headingId}>
      <h2 id={headingId}>People</h2>
      {members.isPending && <p>Loading people…</p>}
      {members.isError && <p role="alert">{errorText(members.error)}</p>}
      {members.isSuccess && (
        <ul className="member-list">
          {members.data.map((member) => (
            <li key={member.user_id}>
              <span className="avatar small" aria-hidden="true">{initials(member.name)}</span>
              <span className="member-who">
                <strong>{member.name}{member.user_id === me.id ? ' (you)' : null}</strong>
                <small className="help">{member.email}</small>
              </span>
              <span className={member.role === 'owner' ? 'pill signal' : 'pill'}>{member.role === 'owner' ? 'Owner' : 'Member'}</span>
              {member.role === 'member' && (member.user_id === me.id ? (
                <button className="link-button" type="button" onClick={leave}>Leave</button>
              ) : canManage && (
                <button className="link-button" type="button" aria-label={`Remove ${member.name}`} onClick={() => remove.mutate(member.user_id)}>Remove</button>
              ))}
            </li>
          ))}
        </ul>
      )}
      {canManage && (
        <form className="inline-add" onSubmit={submit}>
          <div className="field">
            <label htmlFor={emailId}>Add a person by email</label>
            <input id={emailId} type="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
          </div>
          <button className="primary" type="submit" disabled={add.isPending}><UserPlus className="icon" aria-hidden="true" />Add</button>
        </form>
      )}
      <p className="help">They need an account first. Members can start runs, chat and change settings. Only the owner can add people or delete the project.</p>
      {add.isError && <p role="alert">{errorText(add.error)}</p>}
      {remove.isError && <p role="alert">{errorText(remove.error)}</p>}
    </section>
  );
}
