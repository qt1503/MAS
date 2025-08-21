from typing import Literal, Optional, TypedDict
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel
from mint.PoT import ProgramOfThoughtsPrompt
from mint.CoT import ChainOfThoughtPrompt
from mint.PaL import ProgramAidedLanguagePrompt
from mint.Zero_shot import ZeroShotPrompt
from few_shot_PoT import few_shot_gsm8k
from mint.dataset_to_langsmith import DatasetToLangsmith
from mint.testing.test import TestingDataset
from langgraph.checkpoint.memory import MemorySaver
from langsmith import Client
from dotenv import load_dotenv
from streamlit_extras.stylable_container import stylable_container

import argparse
import streamlit as st
import subprocess
import re
import sys
import uuid
import os
import io


class State(TypedDict):
    question: str
    answer: Optional[str]
    context: Optional[str]
    error: Optional[str]
    debug_count: int  

class IntermediateProgram(BaseModel):
    program: str
def single_question(question: str, method: str):
    match(method):
        case "Zero-shot":
            prompt = ZeroShotPrompt()
            st.markdown(f"**Final Answer:** {prompt.solve(question)}")
        case "CoT":
            prompt = ChainOfThoughtPrompt()
            answer, steps = prompt.solve(question)
            st.markdown(f"**Reasoning:**")
            for i, step in enumerate(steps):
                st.write(f"Step {i+1}: {step.explanation} -> {step.output}")
            st.markdown(f"**Final Answer:** {answer}")
        case "PaL":
            prompt = ProgramAidedLanguagePrompt()
            code = prompt.solve(question)
            st.markdown("**Generated code:**")
            st.code(code, language="python")
            st.markdown(f"**Final Answer:** {prompt.exec_node(code)}")
        case "PoT":
            prompt = ProgramOfThoughtsPrompt()
            code = prompt.solve(question, None)
            st.markdown("**Generated code:**")
            st.code(code, language="python")
            st.markdown(f"**Final Answer:** {prompt.exec_node(code)}")
        case "Multi-Agent":
            prompt = ProgramOfThoughtsPrompt()

            builder = StateGraph(State)
            builder.add_node("PreProcessing", PreProcessing)
            builder.add_node("CodeGenerator", lambda state: CodeGenerator(state, prompt))
            builder.add_node("Verifier", lambda state: Verifier(state, prompt))
            builder.add_node("Executor", lambda state: Executor(state, prompt))
            builder.add_node("Debug_Feedback", lambda state: Debug_Feedback(state, prompt))
            builder.add_node("Answer", Answer)

            builder.add_edge(START, "PreProcessing")
            builder.add_edge("PreProcessing", "CodeGenerator")
            builder.add_edge("CodeGenerator", "Verifier")
            builder.add_conditional_edges("Verifier", decide_error)
            builder.add_conditional_edges("Executor", decide_executor)
            builder.add_edge("Debug_Feedback", "CodeGenerator")
            builder.add_edge("Answer", END)
            memory = MemorySaver()
            graph = builder.compile(checkpointer=memory)

            state = State(
                question=question,
                context=None,
                answer=None,
                error=None,
                debug_count=0,
            )

            graph.invoke(
                input=state,
                config={"configurable": {"thread_id": str(uuid.uuid4())}}
            )
        case _:
            pass

def PreProcessing(state: State):
    state["question"] = re.sub(r'\s+', ' ', state["question"].strip())                      
    return {**state}

def CodeGenerator(state: State, prompt):
    if state["error"] is None:
        generated_code = prompt.solve(state["question"], state["context"], few_shot_gsm8k)
        st.markdown("**Generated code:**")
        st.code(generated_code, language="python")
        return {**state, "answer": generated_code}
    else:
        return{**state, "error": None}
    

def Verifier(state: State, prompt):
    error = prompt.check_syntax_and_logic(state["answer"])
    return {**state, "error": error}

def Executor (state: State, prompt):
    result, success = prompt.safe_execute(str(state["answer"]))
    if success:
        return {**state, "answer": result}
    else:
        return {**state, "error": result}

def Debug_Feedback(state: State, prompt):
    error = state.get("error")
    st.markdown(f"**Error:** {error}")
    if state["debug_count"] == 0:
        st.markdown("**First debug attempt:**")
    else:
        st.markdown("**Second debug attempt:**")
    fixed_code = prompt.fix_error(state["answer"], state["error"])
    st.code(fixed_code, language="python")
    return {**state, "debug_count": state.get('debug_count', 0) + 1, "answer": fixed_code}

def Answer(state: State):
    answer = state.get("answer")
    if is_number(answer):
        st.markdown(f"**Final Answer**: {answer}")
        return {**state}
    else:
        st.markdown("**Final Answer**: 9999")
        return {**state, "answer": 9999}  # Return a default value if debug_count == 2
        
def decide_error(state) -> Literal["Executor", "Debug_Feedback"]:
    error = state.get('error', None)
    debug_count = state.get('debug_count', 0)
    if debug_count >= 2:
        return "Executor"
    if error is None:
        return "Executor"
    return "Debug_Feedback"

def decide_executor(state) -> Literal["Answer", "Debug_Feedback"]:
    error = state.get('error', None)
    debug_count = state.get('debug_count', 0)
    if debug_count >= 2:
        return "Answer"
    if error is None:
        return "Answer"
    return "Debug_Feedback"

def is_number(s: str):
    try:
        float(s)   # thử convert sang float
        return True
    except ValueError:
        return False

def check_dataset_exists(dataset_name: str) -> bool:
    """
    Kiểm tra xem dataset đã tồn tại trên LangSmith chưa.
    
    Args:
        dataset_name (str): Tên dataset để kiểm tra
        
    Returns:
        bool: True nếu dataset tồn tại, False nếu không
    """
    try:
        load_dotenv()
        client = Client()
        datasets = client.list_datasets()
        
        for dataset in datasets:
            if dataset.name.upper() == dataset_name.upper():
                return True
        return False
    except Exception as e:
        print(f"⚠️  Không thể kiểm tra dataset trên LangSmith: {e}")
        return False


def testing_dataset(method: str, dataset: str):
    """
    Kiểm tra dataset với phương pháp được chỉ định.
    Trước tiên sẽ kiểm tra xem dataset đã được tạo trên LangSmith chưa.
    """
    # Danh sách các phương pháp và dataset có sẵn
    available_methods = ['Zero_shot', 'PoT', 'CoT', 'PaL', 'MultiAgent']
    available_datasets = ['GSM8K', 'TATQA', 'TABMWP']
    
    # Xác định danh sách dataset cần kiểm tra
    datasets_to_test = []
    if dataset.lower() == 'all':
        datasets_to_test = available_datasets
    else:
        datasets_to_test = [dataset]
    
    # Kiểm tra xem các dataset đã tồn tại trên LangSmith chưa
    for ds in datasets_to_test:
        if not check_dataset_exists(ds):
            print(f"❌ Dataset {ds} chưa được tạo trên LangSmith!")
            print(f"📝 Vui lòng tạo dataset trước bằng lệnh:")
            print(f"   python mathqa.py create-dataset --dataset {ds}")
            print(f"   hoặc")
            print(f"   python mathqa.py create-dataset --dataset all --limit <số_lượng>")
            return
    
    # Xác định danh sách phương pháp cần kiểm tra
    methods_to_test = []
    if method.lower() == 'all':
        methods_to_test = available_methods
    else:
        methods_to_test = [method]
    
    print(f"✅ Tất cả dataset đã sẵn sàng trên LangSmith")
    print(f"🚀 Bắt đầu kiểm tra...")
    print(f"📊 Phương pháp: {', '.join(methods_to_test)}")
    print(f"📚 Dataset: {', '.join(datasets_to_test)}")
    print("-" * 60)
    
    # Chạy kiểm tra cho tất cả các kết hợp method-dataset
    total_tests = len(methods_to_test) * len(datasets_to_test)
    current_test = 0
    
    for test_method in methods_to_test:
        for test_dataset in datasets_to_test:
            current_test += 1
            print(f"\n🔄 Đang chạy kiểm tra {current_test}/{total_tests}: {test_method} trên {test_dataset}")
            print("=" * 50)
            
            try:
                test_case = TestingDataset(method=test_method, dataset=test_dataset)
                test_case.run()
                print(f"✅ Hoàn thành kiểm tra {test_method} trên {test_dataset}")
            except Exception as e:
                print(f"❌ Lỗi khi chạy {test_method} trên {test_dataset}: {str(e)}")
            
            print("=" * 50)
    
    print(f"\n🎉 Đã hoàn thành tất cả {total_tests} kiểm tra!")
    print(f"📈 Kết quả có thể xem trên LangSmith dashboard.")


def create_dataset_to_langsmith(dataset: str, limit: int):
    """
    Tạo dataset trên LangSmith với số lượng sample được chỉ định.
    
    Args:
        dataset (str): Tên dataset ('GSM8K', 'TATQA', 'TABMWP', hoặc 'all')
        limit (int): Số lượng sample để tạo
    """
    if dataset.upper() == "ALL":
        print(f"Đang tạo tất cả các dataset với {limit} samples...")
    else:
        print(f"Đang tạo dataset {dataset} với {limit} samples...")
    
    dataset_creator = DatasetToLangsmith(limit=limit)
    
    if dataset.upper() == "GSM8K":
        result = dataset_creator.create_gsm8k_dataset()
        print(f"✅ Đã tạo thành công dataset GSM8K với ID: {result.id}")
        
    elif dataset.upper() == "TATQA":
        result = dataset_creator.create_tatqa_dataset()
        print(f"✅ Đã tạo thành công dataset TATQA với ID: {result.id}")
        
    elif dataset.upper() == "TABMWP":
        result = dataset_creator.create_tabmwp_dataset()
        print(f"✅ Đã tạo thành công dataset TABMWP với ID: {result.id}")
        
    elif dataset.upper() == "ALL":
        results = dataset_creator.create_all_datasets()
        print("✅ Đã tạo thành công tất cả datasets:")
        for name, ds in results.items():
            print(f"  - {name.upper()}: {ds.id}")
    else:
        raise ValueError(f"Dataset không hợp lệ: {dataset}. Sử dụng 'GSM8K', 'TATQA', 'TABMWP', hoặc 'all'")
        

def main():
    st.markdown("""
    <style>
    /* Nền gradient cho toàn bộ trang */
    .stApp {
        background: linear-gradient(95deg, #76B7B2 0%, #5FA5C8 50%, #3498DB 100%);
        min-height: 100vh;
    }
    header[data-testid="stHeader"] {
        display: none;
    }
    footer {
        display: none;
    }
    .css-14xtw13.e8zbici0 {
        display: none;
    }
    .stButton>button:hover {
        background-color: #FF9AA2;   
        color: white;
        transition: 0.3s;            
    }
    
    /* Đảm bảo container chính tự động mở rộng */
    .main .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
        max-width: none;
    }
    
    /* Style cho kết quả */
    .result-section {
        margin-top: 2rem;
        padding: 1.5rem;
        background: #f8f9fa;
        border-radius: 15px;
        border-left: 4px solid #3498DB;
    }
    
    /* Font size customization */
    /* Title */
    h1 {
        font-size: 2.5rem !important;
        font-weight: 700 !important;
    }
    
    /* Đảm bảo text area và selectbox responsive */
    .stTextArea textarea {
        min-height: 100px !important;
    }
    </style>
    """, unsafe_allow_html=True)

    st.set_page_config(
        page_title="MathQA_MAS", 
        page_icon="💭",              
        layout="centered"               
    )    

    with stylable_container(
        key="styled",
        css_styles="""
            {
                background: white;
                border-radius: 36px;
                padding: 48px;
                width: 100%;
                min-height: 600px;
                overflow: visible;
            }
        """
    ):
        with st.container(horizontal_alignment="center", gap="medium", border=False):
            st.title("MathQA_MAS", anchor=False, width="content")
            question = st.text_area("❓ Question", height="content", placeholder="Please enter your question...")
            method = st.selectbox(
            "🔧 Method",
            ("Zero-shot", "CoT", "PaL", "PoT", "Multi-Agent"),
            index=None,
            placeholder="Select a method...",)
            
            isClicked = st.button("Submit 🚀", width="stretch")

            if isClicked:
                if question.strip() == "" and method is None:
                    st.error("Please enter a question and select a method before submitting.")
                elif question.strip() == "":
                    st.error("Please enter a question before submitting.")
                elif method is None:
                    st.error("Please select a method before submitting.")
                else:
                    st.divider()
                    single_question(question, method)

    parser = argparse.ArgumentParser(description="MathQA")
    print('-' * 50)
    subparsers = parser.add_subparsers(dest='command', title='Danh sách lệnh được hỗ trợ', description="Chọn một trong các lệnh bên dưới để sử dụng")

    # Dataset testing
    test_parser = subparsers.add_parser('test', help='Kiểm tra một tập dữ liệu trên LangSmith')
    test_parser.add_argument("--method", type=str, required=True, help="Phương thức sử dụng", choices=['Zero_shot', 'PoT', 'CoT', 'PaL', 'MultiAgent', 'all'])
    test_parser.add_argument("--dataset", type=str, required=True, help="Tập dữ liệu để kiểm tra", choices=['GSM8K', 'TATQA', 'TABMWP', 'all'])

    # Create dataset on LangSmith
    langsmith_parser = subparsers.add_parser('create-dataset', help='Tạo dataset trên LangSmith')
    langsmith_parser.add_argument("--dataset", type=str, required=True, help="Tập dữ liệu để tạo", choices=['GSM8K', 'TATQA', 'TABMWP', 'all'])
    langsmith_parser.add_argument("--limit", type=int, default=300, help="Số lượng mẫu để tạo (mặc định: 300)")

    # Show datasets
    list_parser = subparsers.add_parser('datasets', help='Hiển thị danh sách các tập dữ liệu có sẵn')

    args = parser.parse_args()

    try:
        if args.command == 'test':
            testing_dataset(args.method, args.dataset)

        elif args.command == 'create-dataset':
            create_dataset_to_langsmith(args.dataset, args.limit)

        elif args.command == 'datasets':
            print("Available datasets:")
            print("-" * 50)
            print("GSM8K - bao gồm 8,500 bài toán toán học chất lượng cao cho học sinh tiểu học do các nhà văn vấn đề con người tạo ra.\n")
            print("TATQA - chứa 16,552 câu hỏi liên quan đến 2,757 ngữ cảnh kết hợp từ các báo cáo tài chính thực tế.\n")
            print("TABMWP - chứa 38,431 bài toán mở cấp độ yêu cầu lý luận toán học trên cả dữ liệu văn bản và bảng.\n")
        else:
            parser.print_help()

    except KeyboardInterrupt:
        print("\nNgười dùng đã ngắt quá trình")
        sys.exit(1)
    except Exception as e:
        print(f"\nLỗi: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()