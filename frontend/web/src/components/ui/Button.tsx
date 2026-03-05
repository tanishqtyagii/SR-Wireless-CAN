import React from "react";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger" | "ghost" | "outline";
  size?: "sm" | "md" | "lg";
  isLoading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className = "", variant = "primary", size = "md", isLoading, children, disabled, ...props }, ref) => {
    const baseStyle = "inline-flex items-center justify-center font-medium rounded transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed";
    
    const variants = {
      primary: "bg-black dark:bg-white text-white dark:text-black hover:bg-gray-800 dark:hover:bg-gray-200 focus:ring-gray-900 dark:focus:ring-white border border-transparent",
      secondary: "bg-gray-100 dark:bg-zinc-800 text-gray-900 dark:text-zinc-100 hover:bg-gray-200 dark:hover:bg-zinc-700 focus:ring-gray-500 dark:focus:ring-zinc-500 border border-transparent",
      danger: "bg-red-600 dark:bg-red-900/40 text-white dark:text-red-400 hover:bg-red-700 dark:hover:bg-red-900/60 focus:ring-red-500 dark:focus:ring-red-500 border border-transparent dark:border-red-900/50",
      ghost: "bg-transparent text-gray-700 dark:text-zinc-400 hover:bg-gray-100 dark:hover:bg-zinc-800 focus:ring-gray-500 dark:focus:ring-zinc-500",
      outline: "bg-white dark:bg-zinc-900 text-gray-700 dark:text-zinc-300 border border-gray-300 dark:border-zinc-700 hover:bg-gray-50 dark:hover:bg-zinc-800 focus:ring-gray-500 dark:focus:ring-zinc-500",
    };
    
    const sizes = {
      sm: "px-3 py-1.5 text-xs",
      md: "px-4 py-2 text-sm",
      lg: "px-6 py-3 text-base",
    };

    return (
      <button
        ref={ref}
        disabled={disabled || isLoading}
        className={`${baseStyle} ${variants[variant]} ${sizes[size]} ${className}`}
        {...props}
      >
        {isLoading && (
          <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-current" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
        )}
        {children}
      </button>
    );
  }
);
Button.displayName = "Button";
