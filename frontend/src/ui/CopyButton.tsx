import { useEffect, useRef, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { IconButton } from './IconButton';

export interface CopyButtonProps {
  text: string;
  label?: string;
  copiedLabel?: string;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

export function CopyButton({
  text,
  label = 'Copy response',
  copiedLabel = 'Copied to clipboard',
  size = 'sm',
  className,
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    };
  }, []);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
      timerRef.current = setTimeout(() => {
        setCopied(false);
      }, 2000);
    } catch {
      // Gracefully ignore clipboard write failures (e.g. permission denied or unsupported environment)
    }
  };

  return (
    <IconButton
      type="button"
      size={size}
      label={copied ? copiedLabel : label}
      icon={copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
      onClick={() => void handleCopy()}
      className={className}
      tone={copied ? 'accent' : 'default'}
    />
  );
}
