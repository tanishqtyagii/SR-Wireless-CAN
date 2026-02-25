import { HTMLAttributes } from "react";

interface InlineAlertProps extends HTMLAttributes<HTMLDivElement> {
  variant?: "error" | "info" | "success" | "warning";
  message: string;
}

export const InlineAlert = ({ variant = "info", message, className = "", ...props }: InlineAlertProps) => {
  if (!message) return null;
  
  const variants = {
    error: "bg-danger-bg text-danger border-danger-border",
    info: "bg-canvas text-text-primary border-border-default",
    success: "bg-success-bg text-success border-success-border",
    warning: "bg-warning-bg text-warning border-warning-border",
  };

  return (
    <div className={`px-3 py-2 text-sm font-medium border rounded-md ${variants[variant]} ${className}`} role="alert" {...props}>
      {message}
    </div>
  );
};
