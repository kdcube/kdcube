export const cleanupCode = (code: string) => {
    // ```(?=[^`]*$)(.|\n)* somehow matches last ```
    // don't ask me how
    return code.trim().replace(/^```.*\n/g, "").replace(/```(?=[^`]*$)(.|\n)*/g, "");
}
export const appendCodeMarkdown = (code: string, language?: string | null) => {
    return `\`\`\`${language ? `${language}` : ""}\n${code}\`\`\``
}

export const getHighlightLanguage = (input: string | null | undefined) => {
    // Strip path and extract extension
    const filename = input?.split("/").pop()?.split("\\").pop();
    if (!filename) return null;
    const ext = filename.includes(".")
        ? filename.slice(filename.lastIndexOf(".") + 1).toLowerCase()
        : filename.toLowerCase();

    const extensionMap: Record<string, string> = {
        // Web
        js: "javascript",
        mjs: "javascript",
        cjs: "javascript",
        jsx: "javascript",
        ts: "typescript",
        tsx: "typescript",
        html: "html",
        htm: "html",
        xhtml: "html",
        css: "css",
        scss: "scss",
        sass: "scss",
        less: "less",
        styl: "stylus",
        graphql: "graphql",
        gql: "graphql",
        wasm: "wasm",

        // Data & Config
        json: "json",
        json5: "json5",
        jsonc: "json",
        yaml: "yaml",
        yml: "yaml",
        toml: "ini",
        ini: "ini",
        env: "ini",
        xml: "xml",
        svg: "xml",
        plist: "xml",
        csv: "dsv",
        tsv: "dsv",

        // Scripting
        py: "python",
        pyw: "python",
        rb: "ruby",
        erb: "ruby",
        rake: "ruby",
        php: "php",
        php3: "php",
        php4: "php",
        php5: "php",
        phtml: "php",
        pl: "perl",
        pm: "perl",
        lua: "lua",
        r: "r",
        rmd: "markdown",
        tcl: "tcl",

        // Shell
        sh: "bash",
        bash: "bash",
        zsh: "bash",
        fish: "bash",
        ksh: "bash",
        ps1: "powershell",
        psm1: "powershell",
        psd1: "powershell",
        bat: "dos",
        cmd: "dos",

        // Systems
        c: "c",
        h: "c",
        cpp: "cpp",
        cc: "cpp",
        cxx: "cpp",
        hpp: "cpp",
        hxx: "cpp",
        cs: "csharp",
        java: "java",
        kt: "kotlin",
        kts: "kotlin",
        swift: "swift",
        go: "go",
        rs: "rust",
        m: "objectivec",
        mm: "objectivec",
        d: "d",
        zig: "zig",

        // JVM / Functional
        scala: "scala",
        sc: "scala",
        groovy: "groovy",
        clj: "clojure",
        cljs: "clojure",
        cljc: "clojure",
        edn: "clojure",
        hs: "haskell",
        lhs: "haskell",
        elm: "elm",
        ex: "elixir",
        exs: "elixir",
        erl: "erlang",
        hrl: "erlang",
        fs: "fsharp",
        fsx: "fsharp",
        fsi: "fsharp",
        ml: "ocaml",
        mli: "ocaml",
        lisp: "lisp",
        lsp: "lisp",
        rkt: "scheme",
        scm: "scheme",

        // Query
        sql: "sql",
        mysql: "sql",
        pgsql: "sql",
        psql: "sql",

        // Markup & Docs
        md: "markdown",
        mdx: "markdown",
        markdown: "markdown",
        rst: "plaintext",
        tex: "latex",
        ltx: "latex",
        latex: "latex",
        adoc: "asciidoc",
        asciidoc: "asciidoc",

        // DevOps & Infra
        dockerfile: "dockerfile",
        tf: "hcl",
        hcl: "hcl",
        nomad: "hcl",
        nix: "nix",
        proto: "protobuf",
        thrift: "thrift",

        // Build
        makefile: "makefile",
        mk: "makefile",
        cmake: "cmake",
        gradle: "groovy",
        bazel: "python",
        bzl: "python",

        // Other
        asm: "x86asm",
        s: "x86asm",
        nasm: "x86asm",
        vb: "vbnet",
        vbs: "vbscript",
        matlab: "matlab",
        jl: "julia",
        dart: "dart",
        nim: "nim",
        cr: "crystal",
        v: "verilog",
        sv: "verilog",
        vhd: "vhdl",
        vhdl: "vhdl",
        sol: "solidity",
        vim: "vim",
        diff: "diff",
        patch: "diff",
        log: "accesslog",
        nginx: "nginx",
        conf: "nginx",
    };

    return extensionMap[ext] ?? null;
}