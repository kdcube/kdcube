import {ReactNode, useEffect, useRef, useState} from 'react';
import {AlertCircle, CheckCircle, HelpCircle, Info, Lightbulb, X, type LucideIcon} from 'lucide-react';

type HintPosition = 'top' | 'bottom' | 'left' | 'right';
type HintTrigger = 'hover' | 'click' | 'click_toggle' | 'focus';
type HintVariant = 'tooltip' | 'popover' | 'info' | 'warning' | 'success' | 'error';
type InlineHintType = 'info' | 'warning' | 'success' | 'error' | 'tip';
type IconHintIcon = 'help' | 'info' | 'warning' | 'tip';

interface HintProps {
    children: ReactNode;
    content: ReactNode;
    position?: HintPosition;
    trigger?: HintTrigger;
    variant?: HintVariant;
    className?: string;
    disabled?: boolean;
    delay?: number;
    autohideDelay?: number;
    offset?: number;
    maxWidth?: string;
    zIndex?: string;
}

// Main Hint Component
function Hint({
                  children,
                  content,
                  position = 'top',
                  trigger = 'hover',
                  variant = 'tooltip',
                  className = '',
                  disabled = false,
                  delay = 300,
                  autohideDelay = 0,
                  offset = 8,
                  maxWidth = 'max-w-xs',
                  zIndex = 'z-50'
              }: HintProps) {
    const [isVisible, setIsVisible] = useState(false);
    const [actualPosition, setActualPosition] = useState<HintPosition>(position);
    const triggerRef = useRef<HTMLDivElement | null>(null);
    const hintRef = useRef<HTMLDivElement | null>(null);
    const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const hideRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // Calculate optimal position based on viewport
    useEffect(() => {
        if (isVisible && triggerRef.current && hintRef.current) {
            const triggerRect = triggerRef.current.getBoundingClientRect();
            const hintRect = hintRef.current.getBoundingClientRect();
            const viewport = {
                width: window.innerWidth,
                height: window.innerHeight
            };

            let optimalPosition: HintPosition = position;

            // Check if hint would go off-screen and adjust
            if (position === 'top' && triggerRect.top - hintRect.height - offset < 0) {
                optimalPosition = 'bottom';
            } else if (position === 'bottom' && triggerRect.bottom + hintRect.height + offset > viewport.height) {
                optimalPosition = 'top';
            } else if (position === 'left' && triggerRect.left - hintRect.width - offset < 0) {
                optimalPosition = 'right';
            } else if (position === 'right' && triggerRect.right + hintRect.width + offset > viewport.width) {
                optimalPosition = 'left';
            }

            setActualPosition(optimalPosition);
        }
    }, [isVisible, position, offset]);

    const showHint = () => {
        if (disabled) return;

        if (delay > 0) {
            timeoutRef.current = setTimeout(() => {
                setIsVisible(true);
            }, delay);
        } else {
            setIsVisible(true);
        }

        if (autohideDelay > 0) {
            hideRef.current = setTimeout(() => {
                setIsVisible(false);
            }, autohideDelay);
        }
    };

    const hideHint = () => {
        if (timeoutRef.current) {
            clearTimeout(timeoutRef.current);
        }
        if (hideRef.current) {
            clearTimeout(hideRef.current);
        }
        setIsVisible(false);
    };

    const toggleHint = () => {
        if (isVisible) {
            hideHint();
        } else {
            showHint();
        }
    };

    const handleTriggerEvents = () => {
        if (trigger === 'hover') {
            return {
                onMouseEnter: showHint,
                onMouseLeave: hideHint,
                onFocus: showHint,
                onBlur: hideHint,
                onClick: hideHint
            };
        } else if (trigger === 'click_toggle') {
            return {
                onClick: toggleHint
            };
        } else if (trigger === 'focus') {
            return {
                onFocus: showHint,
                onBlur: hideHint,
                onClick: hideHint
            };
        } else if (trigger === 'click') {
            return {
                onClick: showHint,
                onMouseLeave: hideHint,
                onBlur: hideHint,
            };
        }
        return {};
    };

    const getPositionClasses = (): string => {
        const positions: Record<HintPosition, string> = {
            top: 'bottom-full left-1/2 transform -translate-x-1/2 mb-0.5',
            bottom: 'top-full left-1/2 transform -translate-x-1/2 mt-0.5',
            left: 'right-full top-1/2 transform -translate-y-1/2 mr-0.5',
            right: 'left-full top-1/2 transform -translate-y-1/2 ml-0.5'
        };
        return positions[actualPosition] || positions.top;
    };

    const getVariantClasses = (): string => {
        const variants: Record<HintVariant, string> = {
            tooltip: 'bg-gray-200 text-black text-sm px-3 py-2 rounded shadow-lg',
            popover: 'bg-white text-gray-900 text-sm p-4 rounded-lg shadow-xl border border-gray-400',
            info: 'bg-blue-50 text-blue-900 text-sm p-3 rounded border border-blue-200',
            warning: 'bg-yellow-50 text-yellow-900 text-sm p-3 rounded border border-yellow-200',
            success: 'bg-green-50 text-green-900 text-sm p-3 rounded border border-green-200',
            error: 'bg-red-50 text-red-900 text-sm p-3 rounded border border-red-200'
        };

        return variants[variant] || variants.tooltip;
    };

    // Cleanup timeout on unmount
    useEffect(() => {
        return () => {
            if (timeoutRef.current) {
                clearTimeout(timeoutRef.current);
            }
        };
    }, []);

    return (
        <div className={`relative inline-block ${className}`}>
            <div
                ref={triggerRef}
                {...handleTriggerEvents()}
            >
                {children}
            </div>

            {isVisible && (
                <>
                    {/* Backdrop for click-outside functionality */}
                    {trigger === 'click_toggle' && (
                        <div
                            className="fixed inset-0 z-40"
                            onClick={hideHint}
                        />
                    )}

                    {/* Hint content */}
                    <div
                        ref={hintRef}
                        className={`absolute ${getPositionClasses()} ${maxWidth} ${zIndex} ${getVariantClasses()} animate-in fade-in-0 zoom-in-95 duration-200 pointer-events-none`}
                        style={{animationFillMode: 'both'}}
                    >
                        {content}

                        {/* Close button for click trigger */}
                        {trigger === 'click' && variant === 'popover' && (
                            <button
                                onClick={hideHint}
                                className="absolute top-2 right-2 text-gray-400 hover:text-gray-600"
                            >
                                <X className="w-4 h-4"/>
                            </button>
                        )}
                    </div>
                </>
            )}
        </div>
    );
}

interface InlineHintProps {
    children: ReactNode;
    type?: InlineHintType;
    className?: string;
}

// Inline Hint Component
function InlineHint({children, type = 'info', className = ''}: InlineHintProps) {
    const [isVisible, setIsVisible] = useState(true);

    const types: Record<InlineHintType, {bgColor: string; textColor: string; borderColor: string; icon: LucideIcon; iconColor: string}> = {
        info: {
            bgColor: 'bg-blue-50',
            textColor: 'text-blue-800',
            borderColor: 'border-blue-200',
            icon: Info,
            iconColor: 'text-blue-500'
        },
        warning: {
            bgColor: 'bg-yellow-50',
            textColor: 'text-yellow-800',
            borderColor: 'border-yellow-200',
            icon: AlertCircle,
            iconColor: 'text-yellow-500'
        },
        success: {
            bgColor: 'bg-green-50',
            textColor: 'text-green-800',
            borderColor: 'border-green-200',
            icon: CheckCircle,
            iconColor: 'text-green-500'
        },
        error: {
            bgColor: 'bg-red-50',
            textColor: 'text-red-800',
            borderColor: 'border-red-200',
            icon: AlertCircle,
            iconColor: 'text-red-500'
        },
        tip: {
            bgColor: 'bg-purple-50',
            textColor: 'text-purple-800',
            borderColor: 'border-purple-200',
            icon: Lightbulb,
            iconColor: 'text-purple-500'
        }
    };

    const typeConfig = types[type] || types.info;
    const Icon = typeConfig.icon;

    if (!isVisible) return null;

    return (
        <div
            className={`flex items-start space-x-3 p-4 rounded-lg border ${typeConfig.bgColor} ${typeConfig.borderColor} ${className}`}>
            <Icon className={`w-5 h-5 mt-0.5 flex-shrink-0 ${typeConfig.iconColor}`}/>
            <div className={`flex-1 ${typeConfig.textColor}`}>
                {children}
            </div>
            <button
                onClick={() => setIsVisible(false)}
                className={`${typeConfig.textColor} hover:opacity-70 flex-shrink-0`}
            >
                <X className="w-4 h-4"/>
            </button>
        </div>
    );
}

interface IconHintProps {
    content: ReactNode;
    icon?: IconHintIcon;
    size?: string;
    variant?: HintVariant;
    position?: HintPosition;
    className?: string;
}

// Icon Hint Component (just the icon with tooltip)
function IconHint({content, icon = 'help', size = 'w-4 h-4', variant = 'tooltip', position = 'top', className = ''}: IconHintProps) {
    const Icon = icon === 'help' ? HelpCircle :
        icon === 'info' ? Info :
            icon === 'warning' ? AlertCircle :
                icon === 'tip' ? Lightbulb : HelpCircle;

    return (
        <Hint content={content} variant={variant} position={position}>
            <Icon className={`${size} text-gray-400 hover:text-gray-600 cursor-help ${className}`}/>
        </Hint>
    );
}

interface FormFieldWithHintProps {
    label: ReactNode;
    hint?: ReactNode;
    error?: ReactNode;
    children: ReactNode;
    required?: boolean;
}

// Form Field with Hint
function FormFieldWithHint({label, hint, error, children, required = false}: FormFieldWithHintProps) {
    return (
        <div className="space-y-2">
            <div className="flex items-center space-x-2">
                <label className="block text-sm font-medium text-gray-700">
                    {label}
                    {required && <span className="text-red-500 ml-1">*</span>}
                </label>
                {hint && (
                    <IconHint
                        content={hint}
                        variant="popover"
                        position="right"
                    />
                )}
            </div>

            {children}

            {error && (
                <p className="text-sm text-red-600 flex items-center space-x-1">
                    <AlertCircle className="w-4 h-4"/>
                    <span>{error}</span>
                </p>
            )}
        </div>
    );
}

interface ProgressiveHintStep {
    title: ReactNode;
    content: ReactNode;
    image?: ReactNode;
}

interface ProgressiveHintProps {
    steps: ProgressiveHintStep[];
    currentStep: number;
    onNext: (next: number) => void;
    onSkip: () => void;
    onComplete: () => void;
}

// Progressive Hint Component (for onboarding)
function ProgressiveHint({steps, currentStep, onNext, onSkip, onComplete}: ProgressiveHintProps) {
    const step = steps[currentStep];

    if (!step || currentStep >= steps.length) {
        return null;
    }

    return (
        <div className="fixed inset-0 bg-black bg-opacity-50 z-50 flex items-center justify-center">
            <div className="bg-white rounded-lg shadow-xl max-w-md p-6">
                <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center space-x-2">
                        <div className="w-8 h-8 bg-blue-500 rounded-full flex items-center justify-center">
                            <span className="text-white text-sm font-bold">{currentStep + 1}</span>
                        </div>
                        <h3 className="font-semibold text-gray-900">{step.title}</h3>
                    </div>
                    <button
                        onClick={onSkip}
                        className="text-gray-400 hover:text-gray-600"
                    >
                        <X className="w-5 h-5"/>
                    </button>
                </div>

                <p className="text-gray-600 mb-6">{step.content}</p>

                {step.image && (
                    <div className="mb-6 p-4 bg-gray-100 rounded-lg text-center">
                        <div className="text-4xl mb-2">{step.image}</div>
                        <p className="text-sm text-gray-500">Visual guide</p>
                    </div>
                )}

                <div className="flex items-center justify-between">
                    <div className="flex space-x-1">
                        {steps.map((_step, index) => (
                            <div
                                key={index}
                                className={`w-2 h-2 rounded-full ${
                                    index === currentStep ? 'bg-blue-500' :
                                        index < currentStep ? 'bg-green-500' : 'bg-gray-300'
                                }`}
                            />
                        ))}
                    </div>

                    <div className="flex space-x-2">
                        {currentStep > 0 && (
                            <button
                                onClick={() => onNext(currentStep - 1)}
                                className="px-4 py-2 text-gray-600 hover:text-gray-800"
                            >
                                Back
                            </button>
                        )}

                        {currentStep < steps.length - 1 ? (
                            <button
                                onClick={() => onNext(currentStep + 1)}
                                className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
                            >
                                Next
                            </button>
                        ) : (
                            <button
                                onClick={onComplete}
                                className="px-4 py-2 bg-green-500 text-white rounded hover:bg-green-600"
                            >
                                Complete
                            </button>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}

export {FormFieldWithHint, ProgressiveHint, InlineHint, IconHint, Hint};
