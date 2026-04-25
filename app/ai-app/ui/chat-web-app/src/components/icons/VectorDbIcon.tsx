interface VectorDbIconProps {
    size?: number;
    className?: string;
}

const VectorDbIcon = ({size = 24, className}: VectorDbIconProps) => {
    return (
        <svg
            width={size}
            height={size}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            className={className}
        >
            {/* Database cylinder */}
            <ellipse cx="10" cy="5" rx="7" ry="2.5"/>
            <path d="M3 5v6c0 1.38 3.13 2.5 7 2.5"/>
            <path d="M3 11v6c0 1.38 3.13 2.5 7 2.5"/>
            <path d="M17 5v3"/>
            <path d="M3 8c0 1.38 3.13 2.5 7 2.5s7-1.12 7-2.5"/>
            {/* Graph nodes */}
            <circle cx="18" cy="11" r="1.5" fill="currentColor" strokeWidth="0"/>
            <circle cx="21" cy="16" r="1.5" fill="currentColor" strokeWidth="0"/>
            <circle cx="18" cy="21" r="1.5" fill="currentColor" strokeWidth="0"/>
            {/* Graph edges */}
            <line x1="18" y1="12.5" x2="20.5" y2="14.5"/>
            <line x1="18" y1="12.5" x2="18" y2="19.5"/>
        </svg>
    );
};

export default VectorDbIcon;
