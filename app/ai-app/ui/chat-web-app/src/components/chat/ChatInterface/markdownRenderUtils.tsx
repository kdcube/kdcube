/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2025 Elena Viter
 */

import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import rehypeSanitize, {defaultSchema} from "rehype-sanitize";
import type {Components} from "react-markdown";
import remarkBreaks from "remark-breaks";
import rehypeRaw from "rehype-raw";
import 'highlight.js/styles/a11y-light.min.css'
import type {PluggableList} from "unified";
import CodeRender from "../markdown/CodeRender.tsx";

const mdSanitizeSchema = {
    ...defaultSchema,
    attributes: {
        ...defaultSchema.attributes,
        // keep language-* class on <code> for highlight.js
        code: [
            ...(defaultSchema.attributes?.code || []),
            ['className', 'language-*']
        ],
        a: [
            ...(defaultSchema.attributes?.a || []),
            ['target', 'rel']
        ],
        img: [
            ...(defaultSchema.attributes?.img || []),
            ['loading', 'width', 'height']
        ],
        input: [
            ...(defaultSchema.attributes?.input || []),
            ['type', 'checked', 'disabled']
        ],
        span: [
            ...(defaultSchema.attributes?.span || []),
            ['className']
        ],
    },
};

export const remarkPlugins = [remarkGfm, remarkBreaks /*, remarkMath*/] as PluggableList;
export const rehypePlugins = [
    rehypeRaw,
    [rehypeSanitize, mdSanitizeSchema],
    rehypeHighlight,
    /* rehypeKatex */
] as PluggableList;

export const markdownComponents: Components = {
    code({className, children, ...props}) {
        return <CodeRender className={className} children={children} {...props}/>;
    },
    a({children, href}) {
        return (
            <a className="text-blue-600 underline cursor-pointer" target="_blank" rel="noreferrer" href={href}>
                {children}
            </a>
        );
    },
    p({children}) {
        return <p className="mt-3 mb-2 last:mb-1 leading-7">{children}</p>;
    },
    hr() {
        return <hr className="my-6 border-t border-gray-400"/>;
    },
    blockquote({children}) {
        return (<blockquote
            className="mt-3 mb-2 last:mb-1 border-l-4 border-gray-300 pl-4 italic text-gray-700">{children}</blockquote>);
    },
    ul({children}) {
        return <ul className="mt-3 mb-2 last:mb-1 list-disc pl-6 space-y-1">{children}</ul>;
    },
    ol({children}) {
        return <ol className="mt-3 mb-2 last:mb-1 list-decimal pl-6 space-y-1">{children}</ol>;
    },
    table({children}) {
        return (<div className="mt-4 mb-1 last:mb-1 overflow-x-auto">
            <table className="min-w-full border-collapse">{children}</table>
        </div>);
    },
    thead({children}) {
        return <thead className="bg-gray-100">{children}</thead>;
    },
    th({children}) {
        return <th className="text-left py-2 px-3 border-b font-medium">{children}</th>;
    },
    td({children}) {
        return <td className="py-2 px-3 border-b align-top">{children}</td>;
    },
    img({src, alt, title}) {
        const s = src || "";
        return (
            <a href={s} target="_blank" rel="noreferrer">
                <img src={s} alt={alt || ""} title={title} loading="lazy" className="my-2 rounded max-w-full h-auto"/>
            </a>
        );
    },

    // Proper heading tags + consistent spacing
    h1({children}) {
        return <h1 className="text-2xl font-bold text-gray-900 mt-4 mb-3">{children}</h1>;
    },
    h2({children}) {
        return <h2 className="text-xl font-semibold text-gray-800 mt-4 mb-3">{children}</h2>;
    },
    h3({children}) {
        return <h3 className="text-lg font-semibold text-gray-700 mt-3 mb-2">{children}</h3>;
    },
    h4({children}) {
        return <h4 className="text-base font-medium text-gray-700 mt-3 mb-2">{children}</h4>;
    },
    h5({children}) {
        return <h5 className="text-base font-medium text-gray-600 mt-2 mb-1">{children}</h5>;
    },
    h6({children}) {
        return <h6 className="text-sm font-medium text-gray-600 mt-2 mb-1">{children}</h6>;
    },

    // Inline formatting niceties (optional)
    em({children}) {
        return <em className="italic">{children}</em>;
    },
    strong({children}) {
        return <strong className="font-semibold">{children}</strong>;
    },
    del({children}) {
        return <del className="line-through">{children}</del>;
    },
};

export const markdownComponentsTight: Components = {
    ...markdownComponents,
    p({children}) {
        return <p className="my-1 last:mb-0 leading-[1.4]">{children}</p>;
    },
    ul({children}) {
        return <ul className="mt-1 mb-1 last:mb-0 list-disc pl-6 space-y-0.5">{children}</ul>;
    },
    ol({children}) {
        return <ol className="mt-1 mb-1 last:mb-0 list-decimal pl-6 space-y-0.5">{children}</ol>;
    },
    blockquote({children}) {
        return (
            <blockquote
                className="mt-1 mb-1 last:mb-0 border-l-4 border-gray-300 pl-4 italic text-gray-700 leading-[1.4]">
                {children}
            </blockquote>
        );
    },
};
