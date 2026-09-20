import type { SystemSettingRow } from '@/api/systemSettings';
import { Badge, type BadgeTone } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { Switch } from '@/ui/Checkbox';
import { Input, Select, Textarea } from '@/ui/Input';
import { PasswordInput } from '@/ui/PasswordInput';
import { cx } from '@/lib/cx';
import styles from './SettingRow.module.css';

const SOURCE_LABELS: Record<SystemSettingRow['source'], string> = {
  override: 'Override',
  environment: '.env',
  default: 'Default',
  missing: 'Missing',
};

const SOURCE_TONES: Record<SystemSettingRow['source'], BadgeTone> = {
  override: 'accent',
  environment: 'info',
  default: 'neutral',
  missing: 'warning',
};

const RISK_TONES: Record<SystemSettingRow['risk'], BadgeTone> = {
  low: 'neutral',
  medium: 'warning',
  high: 'destructive',
};

const LOCKED_REASON: Record<string, string> = {
  container_managed: 'Set by the container and not overridable here.',
  compose_managed: 'Requires editing .env and re-running docker compose up -d.',
};

export interface SettingRowProps {
  setting: SystemSettingRow;
  draft: string | undefined;
  error: string | undefined;
  disabled: boolean;
  onChange: (key: string, value: string) => void;
  onReset: (setting: SystemSettingRow) => void;
}

function describeRange(setting: SystemSettingRow): string | null {
  const { minimum, maximum } = setting;
  if (minimum !== null && maximum !== null) return `${minimum} to ${maximum}`;
  if (minimum !== null) return `at least ${minimum}`;
  if (maximum !== null) return `at most ${maximum}`;
  return null;
}

export function SettingRow({
  setting,
  draft,
  error,
  disabled,
  onChange,
  onReset,
}: SettingRowProps) {
  const locked = !setting.editable;
  const current = draft ?? (setting.secret ? '' : (setting.value ?? ''));
  const edited = draft !== undefined;
  const range = describeRange(setting);

  const hint = setting.secret
    ? setting.configured
      ? 'Configured. Leave blank to keep the current value.'
      : 'Not configured.'
    : range
      ? `Allowed: ${range}.`
      : undefined;

  function handle(value: string) {
    onChange(setting.key, value);
  }

  function renderControl() {
    if (locked) {
      return (
        <p className={styles.lockedValue}>
          {setting.secret
            ? setting.configured
              ? 'Configured'
              : 'Not configured'
            : (setting.value ?? 'Not set')}
        </p>
      );
    }

    if (setting.secret) {
      return (
        <PasswordInput
          label={`${setting.label} value`}
          hideLabel
          hint={hint}
          error={error}
          value={current}
          autoComplete="off"
          placeholder={setting.configured ? 'Leave blank to keep' : 'Not configured'}
          disabled={disabled}
          onChange={(event) => handle(event.target.value)}
        />
      );
    }

    if (setting.kind === 'boolean') {
      return (
        <Switch
          label={`${setting.label} enabled`}
          checked={current.toLowerCase() === 'true'}
          disabled={disabled}
          onChange={(event) => handle(event.target.checked ? 'true' : 'false')}
        />
      );
    }

    if (setting.kind === 'enum') {
      return (
        <Select
          label={`${setting.label} value`}
          hideLabel
          error={error}
          value={current}
          disabled={disabled}
          onChange={(event) => handle(event.target.value)}
        >
          {setting.choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </Select>
      );
    }

    if (setting.kind === 'json' || setting.kind === 'list') {
      return (
        <Textarea
          label={`${setting.label} value`}
          hideLabel
          hint={hint}
          error={error}
          rows={3}
          value={current}
          disabled={disabled}
          spellCheck={false}
          onChange={(event) => handle(event.target.value)}
        />
      );
    }

    const numeric = setting.kind === 'integer' || setting.kind === 'float';
    return (
      <Input
        label={`${setting.label} value`}
        hideLabel
        hint={hint}
        error={error}
        type={numeric ? 'number' : 'text'}
        inputMode={numeric ? 'numeric' : undefined}
        step={setting.kind === 'float' ? 'any' : undefined}
        min={setting.minimum ?? undefined}
        max={setting.maximum ?? undefined}
        value={current}
        disabled={disabled}
        spellCheck={false}
        onChange={(event) => handle(event.target.value)}
      />
    );
  }

  return (
    <article className={cx(styles.row, edited && styles.edited)}>
      <div className={styles.head}>
        <div className={styles.naming}>
          <h3 className={styles.label}>{setting.label}</h3>
          <code className={styles.key}>{setting.key}</code>
        </div>
        <div className={styles.badges}>
          <Badge tone={SOURCE_TONES[setting.source]}>
            {SOURCE_LABELS[setting.source]}
          </Badge>
          {setting.risk !== 'low' ? (
            <Badge tone={RISK_TONES[setting.risk]}>
              {setting.risk === 'high' ? 'High risk' : 'Care needed'}
            </Badge>
          ) : null}
          {edited ? <Badge tone="processing">Unsaved</Badge> : null}
        </div>
      </div>

      <p className={styles.help}>{setting.help}</p>

      <div className={styles.control}>{renderControl()}</div>

      {locked ? (
        <p className={styles.locked}>{LOCKED_REASON[setting.scope]}</p>
      ) : null}

      {!locked && setting.has_override ? (
        <div className={styles.actions}>
          <Button
            variant="ghost"
            size="sm"
            disabled={disabled}
            onClick={() => onReset(setting)}
          >
            {`Reset ${setting.key}`}
          </Button>
        </div>
      ) : null}
    </article>
  );
}
