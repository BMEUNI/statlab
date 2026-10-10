import type { Meter } from '../api/types.ts';
import { meterFill, meterText, meterTone } from '../admin/budget.ts';

export default function BudgetMeter({ meter, label, compact = false }: { meter: Meter; label: string; compact?: boolean }) {
  const classes = ['meter', `meter-${meterTone(meter)}`];
  if (compact) classes.push('compact');
  return (
    <div className={classes.join(' ')}>
      <div className="meter-track" aria-hidden="true">
        <span className="meter-fill" style={{ width: `${meterFill(meter) * 100}%` }} />
      </div>
      <span className="meter-text"><span className="sr-only">{label}: </span>{meterText(meter)}</span>
    </div>
  );
}
