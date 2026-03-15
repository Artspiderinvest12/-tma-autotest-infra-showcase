# smolagents
from smolagents import (
    ActionOutput, ActionStep, ChatMessageStreamDelta, CodeAgent, ToolCall,
    ToolOutput, Generator, CODEAGENT_RESPONSE_FORMAT, Live, Markdown,
    agglomerate_stream_deltas, ChatMessage, LogLevel, AgentGenerationError,
    json, extract_code_from_text, parse_code_blobs, fix_final_answer_code,
    AgentParsingError, Text, Group, AgentExecutionError, truncate_content,
    SystemPromptStep, TaskStep, Timing,
    OpenAIServerModel, TokenUsage, Tool, FinalAnswerStep, PlanningStep, AgentError, handle_agent_output_types
)

from smolagents.models import ChatMessageToolCallStreamDelta

# standard library
from typing import List, Any
import time
YELLOW_HEX = "#d4b702"


class PatteRN_Agent(CodeAgent):
    """
    Оболочка для кодового агента

    """
    @staticmethod
    def preprocess(step: ActionStep) -> str:
        """
        Возвращает Thought + Action + Observation  в понятном для LLM формате суммаризаций.
        """
        thought_action = step.model_output
        observation = step.observations
        number = step.step_number

        return (
            f"Номер шага {number}\n"
            f"Мысли и действия:\n{thought_action}\n"
            f"Результаты логов инструментов:\n {observation}\n"
            f"■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■\n\n")



    def create_optimization_prompt(
        self,
        messages_for_procces: List[str],
        processed_message: str | None,
        tasks: List[str],
    ) -> List[dict[str, str]]:
        """
        Создает переписку для суммаризации истории шагов.
        """

        # Безопасный join без использования \n внутри f-string выражения
        history_joined = "\n".join(messages_for_procces)
        tasks_joined = "\n".join(tasks)

        data_for_process = (
            "История полных 16 шагов:\n"
            + history_joined
            + "\n\n"
        )

        if processed_message:
            data_for_process = (
                "Предыдущая суммаризация:\n"
                + processed_message
                + "\n\n\n"
                + data_for_process
            )

        data_for_process = (
            "Вот задачи **(в том числе добавленные задачи)**, присланные пользователем:\n"
            + tasks_joined
            + "\n\n\n"
            + data_for_process
        )

        system_prompt = SystemPromptStep(
            system_prompt=self.prompt_templates["optimization"]
        ).to_messages()

        system_prompt.extend(
            TaskStep(task=data_for_process).to_messages()
        )

        return system_prompt

    def sort_step_with_new_summarization(self, history_action: ChatMessage, number: int):
        new_steps: List[ActionStep] = []
        task_steps: List[str] = []
        found_old_summary = False

        for step in self.memory.steps:
            if isinstance(step, TaskStep):
                if task_steps:
                    task_steps.append(f"Вот подзадача:\n{step.task}\n\n")
                else:
                    task_steps = [f"Вот изначальная задача:\n{step.task}\n\n"]

            elif isinstance(step, ActionStep):
                if getattr(step, "flag", False):
                    run_start_time = time.time()
                    found_old_summary = True
                    summary = ActionStep(step_number=1, model_output=history_action.content, timing=Timing(start_time=run_start_time, end_time=time.time()))
                    summary.flag = True
                    summary.print_results_for_process = None
                    new_steps.append(summary)
                    continue

                if step.step_number >= number - 2:
                    new_steps.append(step)

        if not found_old_summary:
            run_start_time = time.time()
            summary = ActionStep(step_number=1, model_output=history_action.content, timing=Timing(start_time=run_start_time, end_time=time.time()))
            summary.flag = True
            summary.print_results_for_process = None
            new_steps.insert(0, summary)

        # перенумеровать хвост после summary
        for i, st in enumerate(new_steps, start=1):
            st.step_number = i

        self.memory.steps = [TaskStep(task="\n".join(task_steps))] + new_steps
        

    def optimize_memory(self, number: int):
        """
        Если количество шагодействий равно 16 - начинаем чистку.
        Оптимизация истории шагов заключается в том, чтобы начиная с первого ActionStep до number - 2
        Если Оптимизация уже была сделана ставится флаг на сообщение оптимизации
        """
        
        messages_for_procces = []
        processed_message = None
        tasks = []

        for step in self.memory.steps:
            is_true_processed = getattr(step, "flag", False)
            if isinstance(step, ActionStep) and step.step_number <= number - 2:
                if not is_true_processed:
                    message_for_procces = self.preprocess(step)
                    messages_for_procces.append(message_for_procces)
                else:
                    processed_message = step.model_output
            if isinstance(step, TaskStep):
                tasks.append(step.task)

        
        #Начинаем формировать промпт для обработки
        input_messages = self.create_optimization_prompt(
            messages_for_procces,  
            processed_message, 
            tasks=tasks)


        additional_args: dict[str, Any] = {}
        chat_message: ChatMessage = self.model.generate(
                    input_messages,
                    **additional_args,
                )

        self.sort_step_with_new_summarization(chat_message, number)




    def _run_stream(
        self, task: str, max_steps: int, images: list["PIL.Image.Image"] | None = None
    ) -> Generator[ActionStep | PlanningStep | FinalAnswerStep | ChatMessageStreamDelta]:
        self.step_number = 1
        returned_final_answer = False
        while not returned_final_answer and self.step_number <= max_steps:
            if self.interrupt_switch:
                raise AgentError("Agent interrupted.", self.logger)

            # Run a planning step if scheduled
            if self.planning_interval is not None and (
                self.step_number == 1 or (self.step_number - 1) % self.planning_interval == 0
            ):
                planning_start_time = time.time()
                planning_step = None
                for element in self._generate_planning_step(
                    task, is_first_step=len(self.memory.steps) == 1, step=self.step_number
                ):  # Don't use the attribute step_number here, because there can be steps from previous runs
                    yield element
                    planning_step = element
                assert isinstance(planning_step, PlanningStep)  # Last yielded element should be a PlanningStep
                planning_end_time = time.time()
                planning_step.timing = Timing(
                    start_time=planning_start_time,
                    end_time=planning_end_time,
                )
                self._finalize_step(planning_step)
                self.memory.steps.append(planning_step)

            # Start action step!
            action_step_start_time = time.time()
            action_step = ActionStep(
                step_number=self.step_number,
                timing=Timing(start_time=action_step_start_time),
                # Каждый шаг получает свой собственный список, чтобы
                # observations_images не делился ссылкой между шагами.
                observations_images=list(images) if images else None,
            )
            self.logger.log_rule(f"Step {self.step_number}", level=LogLevel.INFO)
            try:
                for output in self._step_stream(action_step):
                    # Yield all
                    yield output

                    if isinstance(output, ActionOutput) and output.is_final_answer:
                        final_answer = output.output
                        self.logger.log(
                            Text(f"Final answer: {final_answer}", style=f"bold {YELLOW_HEX}"),
                            level=LogLevel.INFO,
                        )

                        if self.final_answer_checks:
                            self._validate_final_answer(final_answer)
                        returned_final_answer = True
                        action_step.is_final_answer = True

            except AgentGenerationError as e:
                # Agent generation errors are not caused by a Model error but an implementation error: so we should raise them and exit.
                raise e
            except AgentError as e:
                # Other AgentError types are caused by the Model, so we should log them and iterate.
                action_step.error = e
            finally:
                self._finalize_step(action_step)
                self.memory.steps.append(action_step)
                yield action_step
                self.step_number += 1

        if not returned_final_answer and self.step_number == max_steps + 1:
            final_answer = self._handle_max_steps_reached(task)
            yield action_step
        yield FinalAnswerStep(handle_agent_output_types(final_answer))

    def _step_stream(
        self, memory_step: ActionStep
    ) -> Generator[ChatMessageStreamDelta | ToolCall | ToolOutput | ActionOutput]:
        """
        Perform one step in the ReAct framework: the agent thinks, acts, and observes the result.
        Yields ChatMessageStreamDelta during the run if streaming is enabled.
        At the end, yields either None if the step is not final, or the final answer.
        """
        memory_messages = self.write_memory_to_messages()

        input_messages = memory_messages.copy()
        ### Generate model output ###

        memory_step.model_input_messages = input_messages
        
        import datetime


        stop_sequences = ["Observation:", "Calling tools:"]
        if self.code_block_tags[1] not in self.code_block_tags[0]:
            # If the closing tag is contained in the opening tag, adding it as a stop sequence would cut short any code generation
            stop_sequences.append(self.code_block_tags[1])
        try:
            additional_args: dict[str, Any] = {}
            if self._use_structured_outputs_internally:
                additional_args["response_format"] = CODEAGENT_RESPONSE_FORMAT
            if self.stream_outputs:
                output_stream = self.model.generate_stream(
                    input_messages,
                    stop_sequences=stop_sequences,
                    **additional_args,
                )
                chat_message_stream_deltas: list[ChatMessageStreamDelta] = []
                with Live("", console=self.logger.console, vertical_overflow="visible") as live:
                    for event in output_stream:
                        chat_message_stream_deltas.append(event)

                        yield event
                chat_message = agglomerate_stream_deltas(chat_message_stream_deltas)
                memory_step.model_output_message = chat_message
                output_text = chat_message.content
            else:


                    
                chat_message: ChatMessage = self.model.generate(
                    input_messages,
                    stop_sequences=stop_sequences,
                    **additional_args,
                )
                memory_step.model_output_message = chat_message
                output_text = chat_message.content
                self.logger.log_markdown(
                    content=output_text or "",
                    title="Output message of the LLM:",
                    level=LogLevel.DEBUG,
                )

            if not self._use_structured_outputs_internally:
                if output_text and not output_text.strip().endswith(self.code_block_tags[1]):
                    output_text += self.code_block_tags[1]
                    memory_step.model_output_message.content = output_text

            memory_step.token_usage = chat_message.token_usage
            memory_step.model_output = output_text
        except Exception as e:
            raise AgentGenerationError(f"Error in generating model output:\n{e}", self.logger) from e

        ### Parse output ###
        try:
            if self._use_structured_outputs_internally:
                code_action = json.loads(output_text)["code"]
                code_action = extract_code_from_text(code_action, self.code_block_tags) or code_action
            else:
                code_action = parse_code_blobs(output_text, self.code_block_tags)
            code_action = fix_final_answer_code(code_action)
            memory_step.code_action = code_action
        except Exception as e:
            error_msg = f"Error in code parsing:\n{e}\nMake sure to provide correct code blobs."
            raise AgentParsingError(error_msg, self.logger)


        memory_step.tool_calls = None

        ### Execute action ###
        self.logger.log_code(title="Executing parsed code:", content=code_action, level=LogLevel.INFO)
        try:
            code_output = self.python_executor(code_action)
            execution_outputs_console = []
            if len(code_output.logs) > 0:
                execution_outputs_console += [
                    Text("Execution logs:", style="bold"),
                    Text(code_output.logs),
                ]
            observation = "Результаты твоего действия:\n" + code_output.logs
        except Exception as e:
            if hasattr(self.python_executor, "state") and "_print_outputs" in self.python_executor.state:
                execution_logs = str(self.python_executor.state["_print_outputs"])
                if len(execution_logs) > 0:
                    execution_outputs_console = [
                        Text("Execution logs:", style="bold"),
                        Text(execution_logs),
                    ]
                    memory_step.observations = "Execution logs:\n" + execution_logs
                    self.logger.log(Group(*execution_outputs_console), level=LogLevel.INFO)
            error_msg = str(e)
            if "Import of " in error_msg and " is not allowed" in error_msg:
                self.logger.log(
                    "[bold red]Warning to user: Code execution failed due to an unauthorized import - Consider passing said import under `additional_authorized_imports` when initializing your CodeAgent.",
                    level=LogLevel.INFO,
                )
            raise AgentExecutionError(error_msg, self.logger)

        truncated_output = truncate_content(str(code_output.output))
        memory_step.observations = observation

        if not code_output.is_final_answer:
            execution_outputs_console += [
                Text(
                    f"Out: {truncated_output}",
                ),
            ]
        self.logger.log(Group(*execution_outputs_console), level=LogLevel.INFO)
        memory_step.action_output = code_output.output
        yield ActionOutput(output=code_output.output, is_final_answer=code_output.is_final_answer)


class Server(OpenAIServerModel):
    """Adjusts completion payload to request high-detail images."""

    def change_detail(self, messages: list[dict[str, Any]] | None = None) -> None:
        if not messages:
            return
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if part.get("type") == "image_url":
                    image_url = part.get("image_url")
                    if isinstance(image_url, dict):
                        image_url["detail"] = "high"
                    elif isinstance(image_url, str):
                        part["image_url"] = {"url": image_url, "detail": "high"}
    
    def generate_stream(
        self,
        messages: list[ChatMessage | dict],
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from: list[Tool] | None = None,
        **kwargs,
    ) -> Generator[ChatMessageStreamDelta]:
        completion_kwargs = self._prepare_completion_kwargs(
            messages=messages,
            stop_sequences=stop_sequences,
            response_format=response_format,
            tools_to_call_from=tools_to_call_from,
            model=self.model_id,
            custom_role_conversions=self.custom_role_conversions,
            convert_images_to_image_urls=True,
            **kwargs,
        )
        self.change_detail(completion_kwargs["messages"])
        completion_kwargs.setdefault("extra_body", {})
        completion_kwargs["extra_body"]["reasoning"] = {"effort": self.reasoning_effort}
        self._apply_rate_limit()




        for event in self.retryer(
            self.client.chat.completions.create,
            **completion_kwargs,
            stream=True,
            stream_options={"include_usage": True},
        ):
            if event.usage:
                yield ChatMessageStreamDelta(
                    content="",
                    token_usage=TokenUsage(
                        input_tokens=event.usage.prompt_tokens,
                        output_tokens=event.usage.completion_tokens,
                    ),
                )
            if event.choices:
                choice = event.choices[0]
                if choice.delta:
                    yield ChatMessageStreamDelta(
                        content=choice.delta.content,
                        tool_calls=[
                            ChatMessageToolCallStreamDelta(
                                index=delta.index,
                                id=delta.id,
                                type=delta.type,
                                function=delta.function,
                            )
                            for delta in choice.delta.tool_calls
                        ]
                        if choice.delta.tool_calls
                        else None,
                    )
                else:
                    if not getattr(choice, "finish_reason", None):
                        raise ValueError(f"No content or tool calls in event: {event}")
