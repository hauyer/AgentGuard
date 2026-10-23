"""Metrics for binary detectors and linear probes."""
from __future__ import annotations
from statistics import mean
from typing import Sequence


def evaluate_binary_classifier(y_true: Sequence[int], scores: Sequence[float], *, threshold: float = 0.5) -> dict:
    labels=list(y_true); values=list(map(float,scores))
    if len(labels) != len(values) or not labels:
        raise ValueError("y_true and scores must have equal non-zero length")
    if set(labels) - {0,1} or set(labels) != {0,1}:
        raise ValueError("AUROC/AUPRC evaluation requires both binary classes")
    if any(not 0 <= v <= 1 for v in values): raise ValueError("scores must be in [0, 1]")
    predictions=[int(v>=threshold) for v in values]
    tp=sum(y==1 and p==1 for y,p in zip(labels,predictions)); tn=sum(y==0 and p==0 for y,p in zip(labels,predictions))
    fp=sum(y==0 and p==1 for y,p in zip(labels,predictions)); fn=sum(y==1 and p==0 for y,p in zip(labels,predictions))
    precision=tp/(tp+fp) if tp+fp else 0.0; recall=tp/(tp+fn) if tp+fn else 0.0
    f1=2*precision*recall/(precision+recall) if precision+recall else 0.0
    positives=sum(labels); negatives=len(labels)-positives
    pos_scores=[s for y,s in zip(labels,values) if y==1]; neg_scores=[s for y,s in zip(labels,values) if y==0]
    auc=sum(1.0 if p>n else 0.5 if p==n else 0.0 for p in pos_scores for n in neg_scores)/(positives*negatives)
    ranked=sorted(zip(values,labels),key=lambda item:item[0],reverse=True)
    seen_pos=0; seen_total=0; previous_recall=0.0; average_precision=0.0; index=0
    while index < len(ranked):
        threshold_score=ranked[index][0]
        end=index
        while end < len(ranked) and ranked[end][0] == threshold_score:
            end += 1
        seen_pos += sum(label for _, label in ranked[index:end])
        seen_total += end-index
        recall_at_threshold=seen_pos/positives
        precision_at_threshold=seen_pos/seen_total
        average_precision += (recall_at_threshold-previous_recall)*precision_at_threshold
        previous_recall=recall_at_threshold
        index=end
    brier=mean((score-label)**2 for score,label in zip(values,labels))
    return {"n":len(labels),"threshold":threshold,"accuracy":(tp+tn)/len(labels),
            "precision":precision,"recall":recall,"f1":f1,"false_positive_rate":fp/negatives,
            "auroc":auc,"auprc_average_precision":average_precision,"brier_score":brier,
            "confusion_matrix":{"tp":tp,"tn":tn,"fp":fp,"fn":fn},
            "limitations":["Metrics describe this evaluation sample only.","Threshold selection must use validation data, not the final test set.","AUROC/AUPRC do not address case-family leakage; split groups before training/evaluation."]}
