# -*- coding: utf-8 -*-

import torch
import torch.nn as nn
from torch.optim import Adam, SGD
from torchcrf import CRF
from transformers import BertModel, BertConfig
from transformers import AutoTokenizer, AutoModel,AutoModelForTokenClassification
from peft import get_peft_model, LoraConfig, TaskType, \
    PromptTuningConfig, PrefixTuningConfig, PromptEncoderConfig 
from torch.optim import Adam, SGD

"""
建立网络模型结构，增加bert模型
"""

class TorchModel(nn.Module):
    def __init__(self, config):
        self.config = config
        super(TorchModel, self).__init__()
        hidden_size = config["hidden_size"]
        vocab_size = config["vocab_size"] + 1
        class_num = config["class_num"]
        pretrain_model_path = config["pretrain_model_path"]
        if config['use_bert']:
            if config['use_peft']:
                model = AutoModelForTokenClassification.from_pretrained(config["pretrain_model_path"], num_labels=class_num)
                # model = BertModel.from_pretrained(pretrain_model_path) 
                # model = AutoModel.from_pretrained(pretrain_model_path) 
                #大模型微调策略
                tuning_tactics = config["tuning_tactics"]
                if tuning_tactics == "lora_tuning":
                    # peft_config = LoraConfig(
                    #     task_type=TaskType.SEQ_CLS,
                    #     inference_mode=False,
                    #     r=16,
                    #     lora_alpha=32,
                    #     lora_dropout=0.05,
                    #     target_modules=["query", "key", "value"]
                    # )
                    peft_config = LoraConfig(
                        r=8,
                        lora_alpha=32,
                        inference_mode=False,
                        target_modules=["query", "key", "value"],
                        lora_dropout=0.05,
                        bias="none",
                        task_type=TaskType.TOKEN_CLS
                    )
                elif tuning_tactics == "p_tuning":
                    peft_config = PromptEncoderConfig(task_type="SEQ_CLS", num_virtual_tokens=10)
                elif tuning_tactics == "prompt_tuning":
                    peft_config = PromptTuningConfig(task_type="SEQ_CLS", num_virtual_tokens=10)
                elif tuning_tactics == "prefix_tuning":
                    peft_config = PrefixTuningConfig(task_type="SEQ_CLS", num_virtual_tokens=10)
                
                # print(model.state_dict().keys())
                self.bert_encoder = get_peft_model(model, peft_config)
                # self.bert_encoder = model
            else:
                self.bert_encoder = BertModel.from_pretrained(pretrain_model_path) 
        # 冻结BERT模型参数
        # if not self.config['bert_requires_grad']:
        #     for param in self.bert_encoder.parameters():
        #         param.requires_grad = False
        # self.gelu = torch.nn.GELU()
        self.embedding = nn.Embedding(vocab_size, hidden_size, padding_idx=0)
        self.layer = nn.LSTM(hidden_size, hidden_size, batch_first=True, bidirectional=True, num_layers=1)
        if self.config["use_bert"]:
            # self.classify = nn.Linear(hidden_size//2, class_num)
            # self.fc1 = nn.Linear(hidden_size, hidden_size//4)
            # self.layer2 = nn.LSTM(hidden_size//4, hidden_size//4, batch_first=True, bidirectional=True, num_layers=1)
            self.classify = nn.Linear(hidden_size, class_num)

        else:
            self.classify = nn.Linear(hidden_size * 2, class_num)
        self.crf_layer = CRF(class_num, batch_first=True)
        # self.crf_layer = nn.CRF(class_num, batch_first=True)
        self.use_crf = config["use_crf"]
        self.loss = torch.nn.CrossEntropyLoss(ignore_index=-1)  #loss采用交叉熵损失

    #当输入真实标签，返回loss值；无真实标签，返回预测值
    def forward(self, x, target=None):
        if self.config["use_peft"]:
            predict = self.bert_encoder(x)[0] 
            # predict 32,60,9 ? 60是什么啊 x 32,50
            # 之后排查了一下，可能是num_virtual_tokens=10，增加了10个虚拟的文本长度
            # 可是这样子增加文本长度后，应该要怎么预测？因为是基于每个token进行预测的，前面填充的token没有标签，这要如何预测？
            # 所以对于序列标注任务不能用这种方式,只能使用lora方式
            # 这里的返回有问题，要去查一下文档
            # 正常的predict是 32，50，9 ; target 32,50
            if target is not None:
                # predict.view(-1, predict.shape[-1]) 32*50,9 
                # target.view(-1) 32*50
                return self.loss(predict.view(-1, predict.shape[-1]), target.view(-1))
            return predict
        if self.config["use_bert"]:
            x = self.bert_encoder(x).last_hidden_state  # [32, 50, 768]
            # x = self.fc1(x)  # bs,50,192
            # x = self.gelu(x)
            # x, _ = self.layer2(x)
        else:
            x = self.embedding(x)  #input shape:(batch_size, sen_len)
            x, _ = self.layer(x)      #input shape:(batch_size, sen_len, input_dim)
        predict = self.classify(x) # 32,50,9               #input shape:(batch_size, input_dim)
        if target is not None:
            if self.use_crf:
                mask = target.gt(-1)
                mask[:, 0] = 1  # 确保第一个时间步的掩码值为 1
                # 最小化上述的负的对数似然损失值。因此在计算梯度和执行反向传播时,我们需要对损失值取相反数,所以乘以负1，不然无法训练
                return -1 *self.crf_layer(predict, target, mask, reduction="mean")
            else:
                return self.loss(predict.view(-1, predict.shape[-1]), target.view(-1))
        else:
            if self.use_crf:
                return self.crf_layer.decode(predict)
            else:
                return predict


def choose_optimizer(config, model):
    optimizer = config["optimizer"]
    learning_rate = config["learning_rate"]
    if optimizer == "adam":
        return Adam(model.parameters(), lr=learning_rate)
    elif optimizer == "sgd":
        return SGD(model.parameters(), lr=learning_rate)


if __name__ == "__main__":
    from config import Config
    model = TorchModel(Config)