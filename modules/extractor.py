import re
import ast
import logging

from tree_sitter import Node, Parser, Language, Query
from tree_sitter_javascript import language as js_grammar

class Extractor:
    JS_LANG = js_grammar()
    LANG_PROCESSOR = Language(JS_LANG)

    def __init__(self, **kwargs) -> None:
        self.logger = logging.getLogger("Extractor")
        self.parser = Parser(Extractor.LANG_PROCESSOR)
        self.array_query = Query(Extractor.LANG_PROCESSOR, """
        (function_declaration
            name: (identifier) @function-name
            body: (statement_block
            (lexical_declaration
                (variable_declarator
                name: (identifier) 
                value: (array) @array-target
        )))) 
        """)

    def get_keys(self, code: bytes) -> list:
        '''
            Assumptions:
                - The last array will contain the strings we need
                - The key segments will be the last 3 arrays

        '''
        tree = self.parser.parse(code)
        target = self.array_query.matches(tree.root_node)[-1][1]
        target_array = ast.literal_eval(target["array-target"].text.decode())
        push_index = target_array.index("push")
        array_length = len(target_array)
        self.logger.debug(target_array)
        self.logger.debug(f"Length: {array_length}")
        self.logger.debug(f"Push index: {push_index}")

        keys = []
        offset = None
        array_lexical_declarations = re.findall(rb"const\s(\w+)\s?=\s?\[\]\;\s?([^;]+)", code)
        
        def format_segment(segment_match):
            index = int(segment_match.group(2))
            target = (index + offset) % array_length
            self.logger.debug(f"Target index (({index} + {offset} <{index + offset}>) % {array_length}): {target}")
            data = target_array[target]
            self.logger.debug(f"Data: {data}")
            return f'"{data}"'

        for identifier, expression in array_lexical_declarations[-3:]:
            self.logger.debug(identifier)
            self.logger.debug(expression)

            push_offsets = re.findall(identifier + rb"\[\w+\((\d+)\)\]", expression)
            if not push_offsets:
                continue
            if not len(set(push_offsets)) == 1:
                raise ValueError("Differing push statement offsets!")

            self.logger.debug(f"Push offset: {push_offsets[0]}")
            offset = push_index - int(push_offsets[0])
            self.logger.debug(f"Offset: {offset}")

            segment = re.sub(r"(\w\((\d+)\))", format_segment, expression.decode())
            segment = segment.replace("[\"push\"]", ".push")
            for seg in segment.split(";"):
                key = "".join(re.findall(r"\.push\(\"(\w+)\"\)", seg))
                self.logger.debug(key)
                if key:
                    keys.append(key)
        
        return keys