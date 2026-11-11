const completions = {
    python: [
        "and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del",
        "elif", "else", "except", "False", "finally", "for", "from", "if", "import", "in",
        "input", "int", "len", "list", "map", "max", "min", "not", "or", "print", "range",
        "return", "set", "sorted", "str", "sum", "True", "tuple", "while", "with",
    ],
    java: [
        "boolean", "break", "byte", "catch", "char", "class", "continue", "double", "else",
        "false", "final", "for", "if", "import", "int", "long", "main", "new", "null",
        "println", "public", "return", "Scanner", "static", "String", "System", "true", "void",
        "while",
    ],
    javascript: [
        "Array", "break", "const", "continue", "else", "false", "for", "function", "if",
        "let", "Math", "new", "Number", "parseInt", "process", "return", "String", "true",
        "undefined", "var", "while",
    ],
    c: [
        "break", "case", "char", "continue", "double", "else", "false", "float", "for",
        "if", "include", "int", "long", "main", "printf", "return", "scanf", "sizeof",
        "struct", "switch", "true", "void", "while",
    ],
    cpp: [
        "bool", "break", "case", "char", "class", "continue", "cout", "cin", "double", "else",
        "false", "float", "for", "if", "include", "int", "long", "main", "namespace", "printf",
        "return", "size_t", "std", "string", "true", "vector", "void", "while",
    ],
};

function getLanguage(languageSelect) {
    const name = languageSelect?.selectedOptions[0]?.textContent?.toLowerCase() || "";
    if (name.includes("python")) return "python";
    if (name.includes("javascript") || name.includes("node.js")) return "javascript";
    if (name.includes("java")) return "java";
    if (name.includes("c++") || name.includes("cpp")) return "cpp";
    if (name.includes("(c ") || name.startsWith("c ") || name.includes("gcc")) return "c";
    return "cpp";
}

function getCodeMirrorMode(language) {
    return {
        python: "python",
        java: "text/x-java",
        javascript: "javascript",
        c: "text/x-csrc",
        cpp: "text/x-c++src",
    }[language];
}

function createHintProvider(language) {
    return (editor) => {
        const cursor = editor.getCursor();
        const token = editor.getTokenAt(cursor);
        const from = window.CodeMirror.Pos(cursor.line, token.start);
        const prefix = token.string.slice(0, cursor.ch - token.start);
        const suggestions = completions[language].filter((word) =>
            word.toLowerCase().startsWith(prefix.toLowerCase()) &&
            word !== token.string &&
            word !== prefix
        );
        return {
            list: suggestions,
            from,
            to: window.CodeMirror.Pos(cursor.line, token.end),
        };
    };
}

export function initCodeEditor(textarea, languageSelect) {
    if (!textarea || !window.CodeMirror) return null;

    const editor = window.CodeMirror.fromTextArea(textarea, {
        lineNumbers: true,
        mode: getCodeMirrorMode(getLanguage(languageSelect)),
        theme: "material-darker",
        indentUnit: 4,
        tabSize: 4,
        indentWithTabs: false,
        lineWrapping: false,
        autoCloseBrackets: true,
        matchBrackets: true,
        indentOnInput: true,
        readOnly: textarea.disabled,
        extraKeys: {
            "Ctrl-Space": (instance) => instance.showHint({
                hint: createHintProvider(getLanguage(languageSelect)),
                completeSingle: false,
            }),
            "Tab": "indentMore",
            "Shift-Tab": "indentLess",
        },
        hintOptions: {
            completeSingle: false,
            closeOnUnfocus: true,
        },
        ...(textarea.maxLength > 0 ? {
            maxLength: textarea.maxLength,
            beforeChange(instance, change) {
                const currentLength = instance.getValue().length;
                const removedLength = change.removed.join("\n").length;
                const insertedLength = change.text.join("\n").length;
                if (currentLength - removedLength + insertedLength > textarea.maxLength) {
                    change.cancel();
                }
            },
        } : {}),
    });

    editor.on("inputRead", (instance, change) => {
        const cursor = instance.getCursor();
        const token = instance.getTokenAt(cursor);
        const prefix = token.string.slice(0, cursor.ch - token.start);
        if (change.text.join("").length && prefix.length >= 2 && /\w$/.test(prefix)) {
            instance.showHint({
                hint: createHintProvider(getLanguage(languageSelect)),
                completeSingle: false,
            });
        }
    });

    const updateLanguage = () => {
        const language = getLanguage(languageSelect);
        editor.setOption("mode", getCodeMirrorMode(language));
    };
    languageSelect?.addEventListener("change", updateLanguage);

    return {
        editor,
        updateLanguage,
    };
}
