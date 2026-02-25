import { HTMLAttributes, forwardRef } from "react";

// Replaces Card with a flatter, tighter structure
export const Panel = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className = "", children, ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={`panel-border flex flex-col ${className}`}
        {...props}
      >
        {children}
      </div>
    );
  }
);
Panel.displayName = "Panel";

export const PanelHeader = ({ className = "", children, ...props }: HTMLAttributes<HTMLDivElement>) => (
  <div className={`px-4 py-3 border-b border-border-default bg-canvas flex flex-col gap-0.5 rounded-t-md shrink-0 ${className}`} {...props}>
    {children}
  </div>
);

export const PanelTitle = ({ className = "", children, ...props }: HTMLAttributes<HTMLHeadingElement>) => (
  <h3 className={`text-sm font-semibold text-text-primary ${className}`} {...props}>
    {children}
  </h3>
);

export const PanelContent = ({ className = "", children, ...props }: HTMLAttributes<HTMLDivElement>) => (
  <div className={`p-4 flex-1 overflow-auto ${className}`} {...props}>
    {children}
  </div>
);
