import kfp
from kfp import dsl
from kfp.dsl import Output, Artifact

@dsl.component(
    base_image='pytorch/pytorch:2.2.1-cuda12.1-cudnn8-runtime',
    packages_to_install=[
        'transformers==4.38.1',
        'peft==0.8.2',
        'bitsandbytes==0.42.0',
        'accelerate==0.27.2',
        'mlflow==2.11.3',
        'sentencepiece==0.2.0'
    ]
)
def train_lora_component(
    model_id: str,
    mlflow_tracking_uri: str,
    hf_token: str,
    run_id_output: Output[Artifact]
):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TrainingArguments, Trainer
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    import mlflow
    import os
    import shutil

    # Set HF token
    os.environ["HUGGING_FACE_HUB_TOKEN"] = hf_token
    
    # MLflow setup
    mlflow.set_tracking_uri(mlflow_tracking_uri)
    mlflow.set_experiment("lite-llm-finetuning")
    
    # 4-bit Quantization Config
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_use_double_quant=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16
    )

    # Load Model
    print(f"Loading model {model_id} in 4-bit...")
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    tokenizer.pad_token = tokenizer.eos_token
    
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True
    )

    # Prepare for LoRA
    model = prepare_model_for_kbit_training(model)
    
    peft_config = LoraConfig(
        r=8,
        lora_alpha=32,
        target_modules=["q_proj", "v_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM"
    )

    model = get_peft_model(model, peft_config)

    # Dummy Dataset for Lite test
    class DummyDataset(torch.utils.data.Dataset):
        def __init__(self, tokenizer):
            text = "Fine-tuning LLMs with QLoRA is efficient."
            self.encodings = tokenizer(text, truncation=True, padding="max_length", max_length=128, return_tensors="pt")
        def __len__(self): return 10
        def __getitem__(self, idx):
            item = {key: val.squeeze() for key, val in self.encodings.items()}
            item['labels'] = item['input_ids'].clone()
            return item

    train_dataset = DummyDataset(tokenizer)

    training_args = TrainingArguments(
        output_dir="./results",
        per_device_train_batch_size=1,
        max_steps=3,
        logging_steps=1,
        learning_rate=2e-4,
        fp16=True,
        report_to="mlflow"
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
    )

    with mlflow.start_run() as run:
        trainer.train()
        # Save only adapters
        print("Saving adapters...")
        adapter_path = "./adapters"
        model.save_pretrained(adapter_path)
        tokenizer.save_pretrained(adapter_path)
        mlflow.log_artifacts(adapter_path, artifact_path="adapters")
        
        with open(run_id_output.path, 'w') as f:
            f.write(run.info.run_id)
    
    print(f"Training complete. Run ID: {run.info.run_id}")

@dsl.pipeline(name="lite-mlops-training")
def mlops_pipeline(
    model_id: str = "microsoft/Phi-3-mini-4k-instruct",
    mlflow_uri: str = "http://mlflow.ml-ops-lite.svc:5000"
):
    train_task = train_lora_component(
        model_id=model_id,
        mlflow_tracking_uri=mlflow_uri,
        hf_token="DUMMY_FOR_NOW" # Will be injected via env var
    )
    # Injecting the secret via KFP env var
    train_task.set_env_variable(name="HF_TOKEN", value_from_secret=dsl.Secret(name="hf-token", key="HF_TOKEN"))
