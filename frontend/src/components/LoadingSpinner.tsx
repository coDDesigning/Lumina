import React from 'react';
import { Loader2 } from 'lucide-react';

interface LoadingSpinnerProps {
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

const sizeMap = {
  sm: 16,
  md: 24,
  lg: 32,
};

const sizeClasses = {
  sm: 'spinner-sm',
  md: 'spinner-md',
  lg: 'spinner-lg',
};

export const LoadingSpinner: React.FC<LoadingSpinnerProps> = ({ 
  size = 'md', 
  className = '' 
}) => {
  return (
    <Loader2
      size={sizeMap[size]}
      className={`animate-spin loading-spinner ${sizeClasses[size]} ${className}`}
      aria-hidden="true"
    />
  );
};
