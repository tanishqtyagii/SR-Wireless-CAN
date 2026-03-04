import React from 'react';
import { VcuState, FlashStatus } from '../../types';

interface StatusPillProps {
  status: VcuState | FlashStatus;
  className?: string;
  size?: 'sm' | 'md';
}

export function StatusPill({ status, className = "", size = "md" }: StatusPillProps) {
  let bg = "bg-gray-100 border-gray-200";
  let text = "text-gray-800";
  let dot = "bg-gray-500";
  let label = status.toUpperCase();

  switch (status) {
    case 'idle':
    case 'success':
      bg = "bg-green-100 border-green-300";
      text = "text-green-800";
      dot = "bg-green-500";
      break;
    case 'bootloading':
    case 'pending':
      bg = "bg-yellow-100 border-yellow-300";
      text = "text-yellow-800";
      dot = "bg-yellow-500 animate-pulse";
      break;
    case 'bootloaded':
      bg = "bg-teal-100 border-teal-300";
      text = "text-teal-800";
      dot = "bg-teal-500";
      label = "BOOTLOADED";
      break;
    case 'flashing':
      bg = "bg-blue-100 border-blue-300";
      text = "text-blue-800";
      dot = "bg-blue-500 animate-pulse";
      break;
    case 'error':
    case 'failed':
      bg = "bg-red-100 border-red-300";
      text = "text-red-800";
      dot = "bg-red-500";
      break;
  }

  const sizeClasses = size === 'sm' 
    ? "px-2 py-0.5 text-[10px]" 
    : "px-2.5 py-1 text-xs";

  return (
    <span className={`inline-flex items-center gap-1.5 font-bold border rounded-sm tracking-wide ${bg} ${text} ${sizeClasses} ${className}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${dot}`}></span>
      {label}
    </span>
  );
}
