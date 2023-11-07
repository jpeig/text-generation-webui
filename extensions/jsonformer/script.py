import json
from itertools import chain
import textwrap
from typing import Dict, Any, Callable, Generator, Optional, TypedDict
import random
import re
import time

import gradio as gr

import modules.text_generation as text_generation
import modules.shared as shared

params = {
    "json_schema": textwrap.dedent("""
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "number"},
                "is_student": {"type": "boolean"},
                "courses": {
                    "type": "array",
                    "items": {"type": "string"},
                    "allowed_empty": true
                }
            }
        }"""),
    "manual_prompt": True,
    "enabled": True,
}

class GenerationSettings(TypedDict):
    temperature: float
    max_new_tokens: Optional[int]

# Largely based on and inspired by https://github.com/1rgs/jsonformer
class Jsonformer:
    def __init__(
        self,
        generation_func: Callable[[str, GenerationSettings], Generator[str, None, None]],
        json_schema: Dict[str, Any],
        prompt: str,
        temperature: float,
        manual_prompt: bool = True,
        max_array_length: int = 10,
    ):
        # Generation func accepts a prompt and generation settings
        self.generation_func = generation_func
        self.json_schema = json_schema
        self.prompt = prompt
        self.temperature = temperature
        self.manual_prompt = manual_prompt
        self.max_array_length = max_array_length

    @classmethod
    def validate_schema(cls, schema: Dict[str, Any]):
        if not isinstance(schema, dict):
            pretty = json.dumps(schema)
            raise ValueError(f"Expected a schema object, but got {pretty}")
        if "type" not in schema:
            pretty = json.dumps(schema)
            raise ValueError(f"Missing `type` field in object: {pretty}")
        if schema["type"] == "object":
            if "properties" not in schema:
                pretty = json.dumps(schema)
                raise ValueError(f"Missing `properties` field in object: {pretty}")
            if not isinstance(schema["properties"], dict):
                pretty = json.dumps(schema)
                raise ValueError(f"Value of `properties` field must be an object in {pretty}")
            for key, value in schema["properties"].items():
                cls.validate_schema(schema["properties"][key])
        elif schema["type"] == "array":
            if "items" not in schema:
                pretty = json.dumps(schema)
                raise ValueError(f"Missing `items` field in array: {pretty}")
            cls.validate_schema(schema['items'])
        elif schema["type"] not in ["string", "number", "boolean"]:
            pretty = json.dumps(schema)
            schema_type = schema["type"]
            raise ValueError(f"Invalid `type` value `{schema_type}` in object: {pretty}")

    def get_next_tokens(
            self, 
            generation_settings: GenerationSettings, 
            stopping_regex: Optional[str] = None, 
            regex_return_group: int = 0,
            prompt_override: Optional[str] = None) -> str:
        prompt = prompt_override or self.get_prompt()
        response_generator = self.generation_func(
            prompt, 
            generation_settings,
        )
        double_quote_regex = r'""'
        for i, response in enumerate(response_generator):
            if re.search(double_quote_regex, response):
                raise ValueError("Detected the sequence \"\"\" in the response")
            if stopping_regex:
                match = re.match(stopping_regex, response)
                if match:
                    return match.group(regex_return_group)
        if shared.stop_everything:
            return ''
        if stopping_regex:
            raise ValueError("Failed to find match for stopping regex before end of response")
        return response
    
    def generate_number(self, temperature: Optional[float] = None, iterations=0) -> float:
        settings = {
            'temperature': temperature or self.temperature,
            'max_new_tokens': None,
        }
        stopping_regex = r'(.+)[,\s\]\}]'
        print("generating number")
        response = self.get_next_tokens(
            settings,
            stopping_regex,
            regex_return_group=1,
        )
        try:
            return float(response)
        except ValueError:
            if shared.stop_everything:
                return ''
            if iterations > 3:
                raise ValueError("Failed to generate a valid number")
            return self.generate_number((temperature or self.temperature) * 1.3, iterations=iterations + 1)

    def generate_boolean(self, temperature: Optional[float] = None, iterations=0) -> bool:
        settings = {
            'temperature': temperature or self.temperature,
            'max_new_tokens': 6,
        }
        # The models have a habit of returning 0/1 for bools sometimes.
        # They usually stop after the first bool to follow their own
        # pattern they've established, but it happens often enough we
        # might as well capture the intent.
        stopping_regex = r'\s*(true|false|[01])'
        print("generating boolean")
        try:
            response = self.get_next_tokens(settings, stopping_regex, regex_return_group=1)
            if response == 'true' or response == '1': return True
            elif response == 'false' or response == '0': return False
        except ValueError:
            if shared.stop_everything:
                return ''
            if iterations <= 3: 
                return self.generate_boolean((temperature or self.temperature) * 1.3, iterations=iterations + 1)
            return False
        if iterations <= 3:
            return self.generate_boolean((temperature or self.temperature) * 1.3, iterations = iterations + 1)
        else:
            return False

    def generate_string(self, temperature: Optional[float] = None, iterations=0) -> str:
        """ Expects progress to already have a leading `"` populated """
        # This is super inefficient and I should probably figure out a more clever way to
        # tell the model to stop at the end of a string.
        settings = {
            'temperature': temperature or self.temperature,
            'max_new_tokens': None,
        }
        stopping_regex = r'(.*)(?<!\\)"'
        print("generating string")
        try:
            return self.get_next_tokens(
                settings,
                stopping_regex,
                regex_return_group=1,
            )
        except ValueError:
            if shared.stop_everything:
                return ''
            if iterations < 2: 
                print("Warning: failed to generate string. Raising temperature...")
                return self.generate_string((temperature or self.temperature * 1.3), iterations = iterations + 1)
            if iterations < 4:
                return "\""
            raise

    def add_to_progress(self, s: str) -> Generator[str, None, None]:
        self.progress += s
        yield self.progress

    def apply_indent(self) -> Generator[str, None, None]:
        yield from self.add_to_progress(' ' * self.indent)

    def increase_indent(self):
        self.indent += 4

    def decrease_indent(self):
        self.indent -= 4

    def apply_newline(self) -> Generator[str, None, None]:
        yield from self.add_to_progress('\n')

    def apply_key(self, key) -> Generator[str, None, None]:
        yield from self.apply_indent()
        yield from self.add_to_progress(''.join(['"', key, '": ']))

    def generate_object(self, properties: Dict[str, Any]) -> Generator[str, None, None]:
        yield from self.add_to_progress('{')
        properties = list(properties.items())
        if not len(properties):
            yield from self.add_to_progress('}')
            return
        self.increase_indent()
        for i, (key, schema) in enumerate(properties):
            yield from self.apply_newline()
            yield from self.generate_value(schema, key)
            if i != len(properties) - 1:
                yield from self.add_to_progress(',')
        yield from self.apply_newline()
        self.decrease_indent()
        yield from self.apply_indent()
        yield from self.add_to_progress('}')

    def generate_array(self, item_schema: Dict[str, Any]) -> Generator[str, None, None]:
        # "peek" at whether the LLM is thinking of generating elements or leaving
        # the array empty by checking what the first couple non-whitespace character are
        # and act accordingly.
        print("generating array")
        first_non_whitespace_characters = self.get_next_tokens(
            {
                'temperature': self.temperature,
                'max_new_tokens': 6,
            },
            stopping_regex=r'\s*([^\s]\s*[^\s])',
            regex_return_group=1
        )
        if first_non_whitespace_characters[-1] == ']':
            # The model wants to render an empty array. So be it.
            yield from self.add_to_progress('[]')
            return
        # The model wanted to render an array, so force it to
        # do so, but with the appropriate types.
        yield from self.add_to_progress('[')
        yield from self.apply_newline()
        self.increase_indent()

        yield from self.apply_indent()
        yield from self.generate_value(item_schema)

        for _ in range(self.max_array_length - 1):
            # Use the model as an oracle as to whether or not it would
            # generate another element by checking whether or not it would
            # next generate a comma.
            # Unfortunately, because models often tokenize end quotes and commas
            # together, if we prompt against a string array that has not been closed,
            # the model will often assume we're at the end of the array. So we have
            # remove the most recent quote marks if present and use that prompt to
            # get the model to accurately tell us what it thinks.
            next_tokens = self.get_next_tokens(
                {
                    'temperature': self.temperature,
                    'max_new_tokens': 3
                },
                prompt_override=self.get_prompt().rstrip('"}')
            )
            will_gen_another_element = ',' in next_tokens[:3]
            if not will_gen_another_element:
                break
            yield from self.add_to_progress(',')
            yield from self.apply_newline()
            yield from self.apply_indent()
            yield from self.generate_value(item_schema)
        yield from self.apply_newline()
        self.decrease_indent()
        yield from self.apply_indent()
        yield from self.add_to_progress(']')
        
    def generate_value(self, schema: Dict[str, Any], key: Optional[str] = None) -> Generator[str, None, None]:
        schema_type = schema["type"]
        if key:
            yield from self.apply_key(key)
        if schema_type == "number":
            yield from self.add_to_progress(str(self.generate_number()))
        elif schema_type == "boolean":
            yield from self.add_to_progress(str(self.generate_boolean()).lower())
        elif schema_type == "string":
            yield from self.add_to_progress('"')
            yield from self.add_to_progress(self.generate_string())
            yield from self.add_to_progress('"')
        elif schema_type == "array":
            # generate array handles its own serialization to self.progress
            yield from self.generate_array(schema["items"])
        elif schema_type == "object":
            # generate_object handles its own serialization to self.progress
            yield from self.generate_object(schema["properties"])
        else:
            raise ValueError(f"Unsupported schema type: {schema_type}")

    def get_prompt(self) -> str:
        if self.manual_prompt:
            template = "{prompt}\n{progress}"
            return template.format(prompt=self.prompt, progress=self.progress)

        template = """{prompt}\nOutput result in the following JSON schema format:\n{schema}\nConsider carefully when to populate arrays and when to leave them empty. Remember empty arrays are often appropriate based on the request.\nResult: {progress}"""
        prompt = template.format(
            prompt=self.prompt,
            schema=json.dumps(self.json_schema),
            progress=self.progress
        )
        return prompt

    def __call__(self) -> Generator[str, None, None]:
        self.progress = ''
        self.indent = 0
        for token in self.generate_value(self.json_schema):
            yield token
            if shared.stop_everything:
                break

def custom_generate_reply(question, original_question, seed, state, stopping_strings, is_chat=False, escape_html=False) -> str:
    """ Overrides the main text generation function """

    # Select text generation function
    generate_func = None
    if shared.model_name == 'None' or shared.model is None:
        print("No model is loaded! Select one in the Model tab.")
        yield ''
        return

    if shared.model.__class__.__name__ in ['LlamaCppModel', 'RWKVModel', 'ExllamaModel', 'Exllamav2Model', 'CtransformersModel']:
        generate_func = text_generation.generate_reply_custom
    else:
        generate_func = text_generation.generate_reply_HF

    shared.stop_everything = False

    if not params['enabled'] or state['json_schema'] == None:
        yield from generate_func(question, original_question, seed, state, stopping_strings, is_chat)
    
    else:
        # Since we generate many times, we need to lock the seed,
        # so we have to account for when the seed is "random" and
        # lock it for the course of the run. It is still random from
        # the user's perspective, but remains fixed for the repeated
        # generations we run.

        params['json_schema'] = state['json_schema']
        locked_seed = int(seed)
        if locked_seed == -1:
            locked_seed = random.randint(1, 2**31)

        def wrapped_generate_func(wrapped_prompt: str, generation_settings: GenerationSettings):
            state_overrides = {'temperature': generation_settings['temperature']}
            if generation_settings['max_new_tokens'] is not None:
                state_overrides['max_new_tokens'] = generation_settings['max_new_tokens']
            wrapped_state = {
                key: value
                for key, value in chain(state.items(), state_overrides.items())
            }
            return generate_func(wrapped_prompt, original_question, locked_seed, wrapped_state, stopping_strings, is_chat)

        jsonformer = Jsonformer(
            generation_func=wrapped_generate_func,
            json_schema=json.loads(params['json_schema']),
            prompt=question,
            temperature=state['temperature'],
            manual_prompt=params['manual_prompt'],
        )
        yield from jsonformer()

def ui():
    with gr.Accordion("Click for more information...", open=False):
        gr.Markdown(textwrap.dedent("""
        ## About
        This extension forces the output to conform to a specified JSON schema (or dies trying).

        ## Schema format
        The schema is formatted in JSON. If you're in a hurry, here's an example schema:

        ```json
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "number"},
                "is_student": {"type": "boolean"},
                "courses": {
                    "type": "array",
                    "items": {"type": "string"},
                    "allowed_empty": true
                }
            }
        }
        ```

        Every value expects a `type` field, which can be one of `object`, `array`, `string`, `number`, `boolean`.
        
        Strings, numbers, and booleans contain no fields other than `type`.

        `object` contains the `properties` field, which is an object where the keys represent field names and the values are the schema of that field.

        `array` contains `item` field, which is a schema object representing the type of the items in the array

        Note that the schema is permissive to extra fields. These fields are ignored by JSONformer, but they may influence the behavior of the LLM, for example, you can forbid or allow empty arrays like in the example. This kind of hinting is not bullet-proof, but is surprisingly effective if utilized with care."""))
    with gr.Row():
        enable_checkbox = gr.Checkbox(params['enabled'], label="Enable JSONformer plugin", info="Disabling this setting causes prompts to be executed normally")

    with gr.Row():
        schema_codebox = gr.Code(params['json_schema'], lines=14, language='json', label='JSON schema', interactive=True)

    with gr.Row():
        manual_prompt_checkbox = gr.Checkbox(params['manual_prompt'], label="Manual prompt", info=textwrap.dedent("""
        USE WITH CAUTION! By default, this plugin appends to your prompt with some extra instructions for the LLM which also contain the schema. So you do not 
        need to include the schema in your prompt manually, nor do you need to specify that the result be in JSON in your prompt. Note that this happens behind 
        the scenes and is invisible to you in the UI. If you would like to override this behavior and maintain full control of your prompt, you can enable this 
        checkbox. The plugin will still enforce the specified JSON schema, but you're on your own for informing the LLM that it needs to conform to the schema. 
        This increases the likliehood of the LLM failing to render and may cause the plugin to crash because the LLM isn't conforming to the schema."""))

    with gr.Row():
        info_box = gr.Textbox("", interactive=False, visible=False, show_label=False)

    with gr.Row():
        save_settings_button = gr.Button("Save JSONformer settings", variant='primary')

    def save_settings(enable, schema, manual_prompt):
        try:
            json_schema = json.loads(schema)
            Jsonformer.validate_schema(json_schema)
            params.update({
                "enabled": enable,
                "json_schema": schema,
                "manual_prompt": manual_prompt,
            })
            now = time.ctime()
            return gr.update(value=f"Successfully saved settings at {now}", visible=True)
        except Exception as e:
            return gr.update(value=f"ERROR saving settings: {e}", visible=True)

    save_settings_button.click(save_settings, [enable_checkbox, schema_codebox, manual_prompt_checkbox], [info_box], api_name="set_jsonformer_settings")