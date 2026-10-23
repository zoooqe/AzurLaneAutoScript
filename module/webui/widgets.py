import copy
import json
import random
import string
from html import escape
from typing import Any, Callable, Dict, Generator, List, Optional, TYPE_CHECKING, Union

from pywebio.exceptions import SessionException
from pywebio.io_ctrl import Output
from pywebio.output import *
from pywebio.pin import pin
from pywebio.session import eval_js, local, run_js
from rich.console import ConsoleRenderable

from module.logger import HTMLConsole, Highlighter, WEB_THEME, logger
from module.webui.lang import t
from module.webui.pin import put_checkbox, put_input, put_select, put_textarea
from module.webui.process_manager import ProcessManager
from module.webui.setting import State
from module.webui.utils import (
    DARK_TERMINAL_THEME,
    LIGHT_TERMINAL_THEME,
    LOG_CODE_FORMAT,
    Switch,
)

if TYPE_CHECKING:
    from module.webui.app import AlasGUI


class ScrollableCode:
    """
    https://github.com/pywebio/PyWebIO/discussions/21
    Deprecated
    """

    def __init__(self, keep_bottom: bool = True) -> None:
        self.keep_bottom = keep_bottom

        self.id = "".join(random.choice(string.ascii_letters) for _ in range(10))
        self.html = (
                """<pre id="%s" class="container-log"><code style="white-space:break-spaces;"></code></pre>"""
                % self.id
        )

    def output(self):
        # .style("display: grid; overflow-y: auto;")
        return put_html(self.html)

    def append(self, text: str) -> None:
        if text:
            run_js(
                """$("#{dom_id}>code").append(text);
            """.format(
                    dom_id=self.id
                ),
                text=str(text),
            )
            if self.keep_bottom:
                self.scroll()

    def scroll(self) -> None:
        run_js(
            r"""$("\#{dom_id}").animate({{scrollTop: $("\#{dom_id}").prop("scrollHeight")}}, 0);
        """.format(
                dom_id=self.id
            )
        )

    def reset(self) -> None:
        run_js(r"""$("\#{dom_id}>code").empty();""".format(dom_id=self.id))

    def set_scroll(self, b: bool) -> None:
        # use for lambda callback function
        self.keep_bottom = b


class RichLog:
    def __init__(self, scope, font_width="0.559") -> None:
        self.scope = scope
        self.font_width = font_width
        self.console = HTMLConsole(
            force_terminal=False,
            force_interactive=False,
            width=80,
            color_system="truecolor",
            markup=False,
            record=True,
            safe_box=False,
            highlighter=Highlighter(),
            theme=WEB_THEME,
        )
        # self.callback_id = output_register_callback(
        #     self._callback_set_width, serial_mode=True)
        # self._callback_thread = None
        # self._width = 80
        self.keep_bottom = True
        if State.theme == "dark":
            self.terminal_theme = DARK_TERMINAL_THEME
        else:
            self.terminal_theme = LIGHT_TERMINAL_THEME

    def render(self, renderable: ConsoleRenderable) -> str:
        with self.console.capture():
            self.console.print(renderable)

        html = self.console.export_html(
            theme=self.terminal_theme,
            clear=True,
            code_format=LOG_CODE_FORMAT,
            inline_styles=True,
        )
        # print(html)
        return html

    def extend(self, text):
        if text:
            run_js(
                """$("#pywebio-scope-{scope}>div").append(text);
            """.format(
                    scope=self.scope
                ),
                text=str(text),
            )
            if self.keep_bottom:
                self.scroll()

    def reset(self):
        run_js(f"""$("#pywebio-scope-{self.scope}>div").empty();""")

    def scroll(self) -> None:
        run_js(
            """$("#pywebio-scope-{scope}").scrollTop($("#pywebio-scope-{scope}").prop("scrollHeight"));
        """.format(
                scope=self.scope
            )
        )

    def set_scroll(self, b: bool) -> None:
        # use for lambda callback function
        self.keep_bottom = b

    def get_width(self):
        js = """
        let canvas = document.createElement('canvas');
        canvas.style.position = "absolute";
        let ctx = canvas.getContext('2d');
        document.body.appendChild(canvas);
        ctx.font = `16px Menlo, consolas, DejaVu Sans Mono, Courier New, monospace`;
        document.body.removeChild(canvas);
        let text = ctx.measureText('0');
        ctx.fillText('0', 50, 50);

        ($('#pywebio-scope-{scope}').width()-16)/\
        $('#pywebio-scope-{scope}').css('font-size').slice(0, -2)/text.width*16;\
        """.format(
            scope=self.scope
        )
        width = eval_js(js)
        return 80 if width is None else 128 if width > 128 else int(width)

    # def _register_resize_callback(self):
    #     js = """
    #     WebIO.pushData(
    #         ($('#pywebio-scope-log').width()-16)/$('#pywebio-scope-log').css('font-size').slice(0, -2)/0.55,
    #         {callback_id}
    #     )""".format(callback_id=self.callback_id)

    # def _callback_set_width(self, width):
    #     self._width = width
    #     if self._callback_thread is None:
    #         self._callback_thread = Thread(target=self._callback_width_checker)
    #         self._callback_thread.start()

    # def _callback_width_checker(self):
    #     last_modify = time.time()
    #     _width = self._width
    #     while True:
    #         if time.time() - last_modify > 1:
    #             break
    #         if self._width == _width:
    #             time.sleep(0.1)
    #             continue
    #         else:
    #             _width = self._width
    #             last_modify = time.time()

    #     self._callback_thread = None
    #     self.console.width = int(_width)

    def put_log(self, pm: ProcessManager) -> Generator:
        yield
        try:
            while True:
                last_idx = len(pm.renderables)
                html = "".join(map(self.render, pm.renderables[:]))
                self.reset()
                self.extend(html)
                counter = last_idx
                while counter < pm.renderables_max_length * 2:
                    yield
                    idx = len(pm.renderables)
                    if idx < last_idx:
                        last_idx -= pm.renderables_reduce_length
                    if idx != last_idx:
                        html = "".join(map(self.render, pm.renderables[last_idx:idx]))
                        self.extend(html)
                        counter += idx - last_idx
                        last_idx = idx
        except SessionException:
            pass


class BinarySwitchButton(Switch):
    def __init__(
            self,
            get_state,
            label_on,
            label_off,
            onclick_on,
            onclick_off,
            scope,
            color_on="success",
            color_off="secondary",
    ):
        """
        Args:
            get_state:
                (Callable):
                    return True to represent state `ON`
                    return False tp represent state `OFF`
                (Generator):
                    yield True to change btn state to `ON`
                    yield False to change btn state to `OFF`
            label_on: label to show when state is `ON`
            label_off:
            onclick_on: function to call when state is `ON`
            onclick_off:
            color_on: button color when state is `ON`
            color_off:
            scope: scope for button, just for button **only**
        """
        self.scope = scope
        status = {
            0: {
                "func": self.update_button,
                "args": (
                    label_off,
                    onclick_off,
                    color_off,
                ),
            },
            1: {
                "func": self.update_button,
                "args": (
                    label_on,
                    onclick_on,
                    color_on,
                ),
            },
        }
        super().__init__(status=status, get_state=get_state, name=scope)

    def update_button(self, label, onclick, color):
        clear(self.scope)
        put_button(label=label, onclick=onclick, color=color, scope=self.scope)


# aside buttons


def put_icon_buttons(
        icon_html: str,
        buttons: List[Dict[str, str]],
        onclick: Union[List[Callable[[], None]], Callable[[], None]],
) -> Output:
    value = buttons[0]["value"]
    return put_column(
        [
            output(put_html(icon_html)).style(
                "z-index: 1; margin-left: 8px;text-align: center"
            ),
            put_buttons(buttons, onclick).style(f"z-index: 2; --aside-{value}--;"),
        ],
        size="0",
    )


def put_none() -> Output:
    return put_html("<div></div>")


T_Output_Kwargs = Dict[str, Union[str, Dict[str, Any]]]


def get_title_help(kwargs: T_Output_Kwargs) -> Output:
    title: str = kwargs.get("title")
    help_text: str = kwargs.get("help")

    if help_text:
        res = put_column(
            [
                put_text(title).style("--arg-title--"),
                put_text(help_text).style("--arg-help--"),
            ],
            size="auto 1fr",
        )
    else:
        res = put_text(title).style("--arg-title--")

    return res


# args input widget
def put_arg_input(kwargs: T_Output_Kwargs) -> Output:
    name: str = kwargs["name"]
    options: List = kwargs.get("options")
    if options is not None:
        kwargs.setdefault("datalist", options)

    return put_scope(
        f"arg_container-input-{name}",
        [
            get_title_help(kwargs),
            put_input(**kwargs).style("--input--"),
        ],
    )


def product_stored_row(kwargs: T_Output_Kwargs, key, value):
    kwargs = copy.copy(kwargs)
    kwargs["name"] += f'_{key}'
    kwargs["value"] = value
    return put_input(**kwargs).style("--input--")


def put_arg_stored(kwargs: T_Output_Kwargs) -> Output:
    name: str = kwargs["name"]
    kwargs["disabled"] = True

    values = kwargs.pop("value", {})
    time_ = values.pop("time", "")

    rows = [product_stored_row(kwargs, key, value) for key, value in values.items() if value]
    if time_:
        rows += [product_stored_row(kwargs, "time", time_)]
    return put_scope(
        f"arg_container-stored-{name}",
        [
            get_title_help(kwargs),
            put_scope(
                f"arg_stored-stored-value-{name}",
                rows,
            )
        ]
    )


def put_arg_select(kwargs: T_Output_Kwargs) -> Output:
    name: str = kwargs["name"]
    value: str = kwargs["value"]
    options: List[str] = kwargs["options"]
    options_label: List[str] = kwargs.pop("options_label", [])
    disabled: bool = kwargs.pop("disabled", False)
    _: str = kwargs.pop("invalid_feedback", None)

    if disabled:
        option = [{
            "label": next((opt_label for opt, opt_label in zip(options, options_label) if opt == value), value),
            "value": value,
            "selected": True,
        }]
    else:
        option = [{
            "label": opt_label,
            "value": opt,
            "select": opt == value,
        } for opt, opt_label in zip(options, options_label)]
    kwargs["options"] = option

    return put_scope(
        f"arg_container-select-{name}",
        [
            get_title_help(kwargs),
            put_select(**kwargs).style("--input--"),
        ],
    )


TIME_STEP_MINUTES = 30


def time_options(step: int = TIME_STEP_MINUTES) -> List[str]:
    """Every "HH:MM" of a day, one per `step` minutes."""
    if not isinstance(step, int) or isinstance(step, bool) or step <= 0 or 60 % step:
        step = TIME_STEP_MINUTES
    return [
        f'{minute // 60:02d}:{minute % 60:02d}'
        for minute in range(0, 24 * 60, step)
    ]


def put_arg_time(kwargs: T_Output_Kwargs) -> Output:
    """An "HH:MM" argument picked from a dropdown instead of typed.

    The value crosses into the config as a plain string, so the task code and
    hand-edited configs keep working. `step` comes from argument.yaml and is the
    minute interval between two choices, 30 by default. A value that is off the
    grid stays selectable as its own entry instead of being snapped.
    """
    name: str = kwargs["name"]
    value = kwargs.get("value")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = f'{int(value) % 24:02d}:00'
    step: int = kwargs.pop("step", TIME_STEP_MINUTES)
    kwargs.pop("options", None)
    kwargs.pop("options_label", None)
    kwargs.pop("invalid_feedback", None)

    options = time_options(step)
    if value not in options:
        options = [value] + options
    kwargs["value"] = value
    kwargs["options"] = [
        {"label": option, "value": option, "select": option == value}
        for option in options
    ]

    return put_scope(
        f"arg_container-time-{name}",
        [
            get_title_help(kwargs),
            put_select(**kwargs).style("--input--"),
        ],
    )


def put_arg_state(kwargs: T_Output_Kwargs) -> Output:
    name: str = kwargs["name"]
    value: str = kwargs["value"]
    options: List[str] = kwargs["options"]
    options_label: List[str] = kwargs.pop("options_label", [])
    _: str = kwargs.pop("invalid_feedback", None)
    bold: bool = value in kwargs.pop("option_bold", [])
    light: bool = value in kwargs.pop("option_light", [])

    option = [{
        "label": next((opt_label for opt, opt_label in zip(options, options_label) if opt == value), value),
        "value": value,
        "selected": True,
    }]
    if bold:
        kwargs["class"] = "form-control state state-bold"
    elif light:
        kwargs["class"] = "form-control state state-light"
    else:
        kwargs["class"] = "form-control state"
    kwargs["options"] = option

    return put_scope(
        f"arg_container-select-{name}",
        [
            get_title_help(kwargs),
            put_select(**kwargs).style("--input--"),
        ],
    )


def put_arg_textarea(kwargs: T_Output_Kwargs) -> Output:
    name: str = kwargs["name"]
    mode: str = kwargs.pop("mode", None)
    kwargs.setdefault(
        "code", {"lineWrapping": True, "lineNumbers": False, "mode": mode}
    )

    return put_scope(
        f"arg_contianer-textarea-{name}",
        [
            get_title_help(kwargs),
            put_textarea(**kwargs),
        ],
    )


def put_arg_checkbox(kwargs: T_Output_Kwargs) -> Output:
    # Not real checkbox, use as a switch (on/off)
    name: str = kwargs["name"]
    value: str = kwargs["value"]
    _: str = kwargs.pop("invalid_feedback", None)

    kwargs["options"] = [{"label": "", "value": True, "selected": value}]
    return put_scope(
        f"arg_container-checkbox-{name}",
        [
            get_title_help(kwargs),
            put_checkbox(**kwargs).style("text-align: center"),
        ],
    )


def put_arg_datetime(kwargs: T_Output_Kwargs) -> Output:
    name: str = kwargs["name"]
    return put_scope(
        f"arg_container-datetime-{name}",
        [
            get_title_help(kwargs),
            put_input(**kwargs).style("--input--"),
        ],
    )


def put_arg_storage(kwargs: T_Output_Kwargs) -> Optional[Output]:
    name: str = kwargs["name"]
    if kwargs["value"] == {}:
        return None

    kwargs["value"] = json.dumps(
        kwargs["value"], indent=2, ensure_ascii=False, sort_keys=False, default=str
    )
    kwargs.setdefault(
        "code", {"lineWrapping": True, "lineNumbers": False, "mode": "json"}
    )

    def clear_callback():
        alasgui: "AlasGUI" = local.gui
        alasgui.modified_config_queue.put(
            {"name": ".".join(name.split("_")), "value": {}}
        )
        # https://github.com/pywebio/PyWebIO/issues/459
        # pin[name] = "{}"

    return put_scope(
        f"arg_container-storage-{name}",
        [
            put_textarea(**kwargs),
            put_html(
                f'<button class="btn btn-outline-warning btn-block">{t("Gui.Text.Clear")}</button>'
            ).onclick(clear_callback),
        ],
    )


def put_arg_island_items(kwargs: T_Output_Kwargs) -> Output:
    """Item picker for island item mappings.

    The config still stores a YAML text like `小麦 (2000): 300`, so island tasks
    and the production planner are untouched, but the user picks items from a
    dropdown instead of typing key-value pairs by hand.
    """
    from module.island.item_editor import (
        format_amount,
        is_valid_item_id,
        item_label,
        item_options,
        items_from_text,
        items_to_text,
        parse_amount,
        set_item,
        sorted_items,
    )

    name: str = kwargs["name"]
    lang: str = kwargs.get("lang", "cn")
    _: str = kwargs.pop("invalid_feedback", None)
    # {item_id: amount}, mutated in place by the callbacks below
    items = items_from_text(kwargs.get("value"))

    item_pin = f"{name}_Item"
    amount_pin = f"{name}_Amount"
    list_scope = f"island_items_list-{name}"

    ROW_HEIGHT = "1.7rem"
    ROW_BUTTON_STYLE = (
        f"height: {ROW_HEIGHT}; min-height: {ROW_HEIGHT}; padding: 0 .4rem;"
        " margin: 0; display: flex; align-items: center;"
        " justify-content: center; font-size: .8125rem; line-height: 1;"
    )

    def save():
        alasgui: "AlasGUI" = local.gui
        alasgui.modified_config_queue.put(
            {"name": ".".join(name.split("_")), "value": items_to_text(items)}
        )

    def make_row(item_id, amount):
        def edit_callback(*_):
            pin[item_pin] = str(item_id)
            pin[amount_pin] = format_amount(amount)

        def delete_callback(*_):
            items.pop(int(item_id), None)
            save()
            render_rows()

        # A fixed button height keeps the edit and delete buttons the same
        # size, and zero vertical padding keeps rows compact: put_text() renders
        # a <p> whose default bottom margin was adding the extra row spacing.
        label = escape(item_label(item_id, lang))
        return put_row(
            [
                put_html(
                    f'<div class="island-item-name" title="{label}"'
                    f' style="overflow: hidden; text-overflow: ellipsis;'
                    f' white-space: nowrap; line-height: {ROW_HEIGHT}">{label}</div>'
                ),
                put_html(
                    f'<div class="island-item-amount"'
                    f' style="text-align: right; white-space: nowrap;'
                    f' padding-right: .5rem; font-variant-numeric: tabular-nums;'
                    f' line-height: {ROW_HEIGHT}">{format_amount(amount)}</div>'
                ),
                put_row(
                    [
                        put_html(
                            f'<button class="btn btn-outline-primary"'
                            f' style="{ROW_BUTTON_STYLE}">'
                            f'{t("Gui.Text.IslandItemsEdit")}</button>'
                        ).onclick(edit_callback),
                        put_html(
                            f'<button class="btn btn-outline-warning"'
                            f' style="{ROW_BUTTON_STYLE}">'
                            f'{t("Gui.Text.IslandItemsDelete")}</button>'
                        ).onclick(delete_callback),
                    ],
                    size="1fr 1fr",
                ).style(f"gap: .25rem; height: {ROW_HEIGHT}"),
            ],
            size=f"minmax(9rem, 15rem) 4.5rem 7.5rem",
        ).style(f"align-items: center; padding: 0; margin: 0; height: {ROW_HEIGHT}")

    def collect_rows():
        return [make_row(item_id, amount) for item_id, amount in sorted_items(items)]

    def render_rows():
        rows = collect_rows()
        with use_scope(list_scope, clear=True):
            if rows:
                # size='auto': never let the grid squeeze rows with `1fr`
                put_column(rows, size="auto")
            else:
                put_text(t("Gui.Text.IslandItemsEmpty")).style("--arg-help--")

    def add_callback(*_):
        item_id = pin[item_pin]
        if not item_id or not is_valid_item_id(item_id):
            toast(t("Gui.Text.IslandItemsInvalidItem"), duration=2, color="warn")
            return
        amount = parse_amount(pin[amount_pin])
        if amount is None or amount <= 0:
            toast(t("Gui.Text.IslandItemsInvalidAmount"), duration=2, color="warn")
            return
        set_item(items, item_id, amount)
        save()
        render_rows()
        pin[amount_pin] = ""

    def clear_callback(*_):
        items.clear()
        save()
        render_rows()

    options = [{"label": t("Gui.Text.IslandItemsPick"), "value": ""}]
    options += [
        {"label": label, "value": value} for value, label in item_options(lang)
    ]

    initial_rows = collect_rows()
    if not initial_rows:
        initial_rows = [
            put_text(t("Gui.Text.IslandItemsEmpty")).style("--arg-help--")
        ]

    return put_scope(
        # Do NOT prefix with `arg_container-`: assets/gui/css/alas-pc.css turns
        # those scopes into a 2-column grid (`1fr 13rem`), which squeezes this
        # widget into 13rem and wraps item names one character per line.
        f"island_items-{name}",
        [
            get_title_help(kwargs),
            put_row(
                [
                    put_select(name=item_pin, options=options, value=""),
                    put_input(
                        name=amount_pin,
                        type="text",
                        placeholder=t("Gui.Text.IslandItemsAmount"),
                    ),
                    put_html(
                        f'<button class="btn btn-primary"'
                        f' style="{ROW_BUTTON_STYLE} width: 100%">'
                        f'{t("Gui.Text.IslandItemsAdd")}</button>'
                    ).onclick(add_callback),
                ],
                size="minmax(8rem, 1fr) 8rem 6.5rem",
            ).style(f"max-width: 28rem; height: {ROW_HEIGHT}; margin: .25rem 0 .375rem"),
            put_scope(
                list_scope, [put_column(initial_rows, size="auto")]
            ).style(
                "max-width: 28rem; max-height: 22rem; overflow-y: auto;"
                " border: 1px solid rgba(128, 128, 128, .35);"
                " border-radius: .25rem; padding: .25rem .5rem"
            ),
            put_html(
                f'<button class="btn btn-outline-warning btn-sm"'
                f' style="margin-top: .375rem">'
                f'{t("Gui.Text.IslandItemsClear")}</button>'
            ).onclick(clear_callback),
        ],
    )


_widget_type_to_func: Dict[str, Callable] = {
    "input": put_arg_input,
    "lock": put_arg_state,
    "datetime": put_arg_input,  # TODO
    "select": put_arg_select,
    "time": put_arg_time,
    "textarea": put_arg_textarea,
    "checkbox": put_arg_checkbox,
    "storage": put_arg_storage,
    "state": put_arg_state,
    "stored": put_arg_stored,
    "island_items": put_arg_island_items,
}


def put_output(output_kwargs: T_Output_Kwargs) -> Optional[Output]:
    widget_type = output_kwargs.get("widget_type")
    func = _widget_type_to_func.get(widget_type)
    if func is None:
        # Fallback instead of raising KeyError: a config generated by a newer
        # version must not break page rendering of the whole task group.
        logger.warning(f"Unknown widget type: {widget_type}, fallback to textarea")
        func = put_arg_textarea
    return func(output_kwargs)



def get_loading_style(shape: str, fill: bool) -> str:
    if fill:
        return f"--loading-{shape}-fill--"
    else:
        return f"--loading-{shape}--"


def put_loading_text(
        text: str,
        shape: str = "border",
        color: str = "dark",
        fill: bool = False,
        size: str = "auto 2px 1fr",
):
    loading_style = get_loading_style(shape=shape, fill=fill)
    return put_row(
        [
            put_loading(shape=shape, color=color).style(loading_style),
            None,
            put_text(text),
        ],
        size=size,
    )
