import { useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { IconButton } from './IconButton';
import { Input, type InputProps } from './Input';

export type PasswordInputProps = Omit<InputProps, 'type' | 'action'>;

export function PasswordInput({ disabled, ...rest }: PasswordInputProps) {
  const [showPassword, setShowPassword] = useState(false);

  return (
    <Input
      {...rest}
      type={showPassword ? 'text' : 'password'}
      disabled={disabled}
      action={
        <IconButton
          label={showPassword ? 'Hide password' : 'Show password'}
          icon={showPassword ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
          size="sm"
          type="button"
          onClick={() => setShowPassword((prev) => !prev)}
          disabled={disabled}
        />
      }
    />
  );
}
