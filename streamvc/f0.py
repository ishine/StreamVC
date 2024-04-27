import torch
import torch.nn as nn
import torch.nn.functional as F
import typing as T
import numpy as np

def estimate(
    signal: torch.Tensor,
    sample_rate: float,
    frame_length: int,
    frame_stride: int,
    pitch_max: float = 20000,
    threshold: float = 0.1,
) -> torch.Tensor:
    """estimate the pitch (fundamental frequency) of a signal

    This function attempts to determine the pitch of a signal via the
    Yin algorithm. Accuracy can be improved by sampling the signal at a
    higher rate, especially for higher-frequency pitches, and by narrowing
    the values of pitch_min and pitch_max. For example, good values for
    speech signals are pitch_min=60 and pitch_max=500. frame_stride can also
    be tuned to the expected minimum rate of pitch change of the signal:
    10ms is commonly used for speech.

    The speed and memory usage of the algorithm are also determined by the
    pitch_min parameter, which is used to window the audio signal into
    2*sample_rate/pitch_min sliding windows. A higher pitch_min corresponds to
    less memory usage and faster running time.

    Args:
        signal: the signal vector (1D) or [batch, time] tensor to analyze
        sample_rate: sample rate, in Hz, of the signal
        pitch_min: expected lower bound of the pitch
        pitch_max: expected upper bound of the pitch
        frame_stride: overlapping window stride, in seconds, which determines
            the number of pitch values returned
        threshold: harmonic threshold value (see paper)

    Returns:
        pitch: PyTorch tensor of pitch estimations, one for each frame of
            the windowed signal, an entry of 0 corresponds to a non-periodic
            frame, where no periodic signal was detected

    """

    signal = torch.as_tensor(signal)

    # convert frequencies to samples, ensure windows can fit 2 whole periods
    tau_min = int(sample_rate / pitch_max)
    tau_max = int(frame_length / 2)
    assert tau_min < tau_max

    # compute the fundamental periods
    frames = _frame(signal, frame_length, frame_stride)
    cmdf = _diff(frames, tau_max)[..., tau_min:]
    tau = _search(cmdf, tau_max, threshold)

    # convert the periods to frequencies (if periodic) and output
    return torch.where(
        tau > 0,
        sample_rate / (tau + tau_min + 1).type(signal.dtype),
        torch.tensor(0, device=tau.device).type(signal.dtype),
    )


def _frame(signal: torch.Tensor, frame_length: int, frame_stride: int) -> torch.Tensor:
    # window the signal into overlapping frames, padding to at least 1 frame
    if signal.shape[-1] < frame_length:
        signal = torch.nn.functional.pad(signal, [0, frame_length - signal.shape[-1]])
    return signal.unfold(dimension=-1, size=frame_length, step=frame_stride)


def _diff(frames: torch.Tensor, tau_max: int) -> torch.Tensor:
    # compute the frame-wise autocorrelation using the FFT
    fft_size = 2 ** (-int(-np.log(frames.shape[-1]) // np.log(2)) + 1)
    fft = torch.fft.rfft(frames, fft_size, dim=-1)
    corr = torch.fft.irfft(fft * fft.conj())[..., :tau_max]

    # difference function (equation 6)
    sqrcs = torch.nn.functional.pad((frames * frames).cumsum(-1), [1, 0])
    corr_0 = sqrcs[..., -1:]
    corr_tau = sqrcs.flip(-1)[..., :tau_max] - sqrcs[..., :tau_max]
    diff = corr_0 + corr_tau - 2 * corr

    # cumulative mean normalized difference function (equation 8)
    return (
        diff[..., 1:]
        * torch.arange(1, diff.shape[-1], device=diff.device)
        / torch.maximum(
            diff[..., 1:].cumsum(-1),
            torch.tensor(1e-5, device=diff.device),
        )
    )


def _search(cmdf: torch.Tensor, tau_max: int, threshold: float) -> torch.Tensor:
    # mask all periods after the first cmdf below the threshold
    # if none are below threshold (argmax=0), this is a non-periodic frame
    first_below = (cmdf < threshold).int().argmax(-1, keepdim=True)
    first_below = torch.where(first_below > 0, first_below, tau_max)
    beyond_threshold = torch.arange(cmdf.shape[-1], device=cmdf.device) >= first_below

    # mask all periods with upward sloping cmdf to find the local minimum
    increasing_slope = torch.nn.functional.pad(cmdf.diff() >= 0.0, [0, 1], value=1)

    # find the first period satisfying both constraints
    return (beyond_threshold & increasing_slope).int().argmax(-1)


class F0Estimator(nn.Module):
    def __init__(self, sample_rate: int, frame_length: int, whitening: bool = True):
        super().__init__()
        self.frame_length = frame_length
        self.whitening = whitening
        self.sample_rate = sample_rate

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.pad(x, (self.frame_length, self.frame_length), "constant", 0)
        return estimate(x, self.sample_rate, self.frame_length * 3, self.frame_length)


if __name__ == '__main__':
    x = torch.rand(4,1,320000)
    F0 = F0Estimator(16000, 320)
    print(F0.forward(x).shape)

    # a = estimate(x, 320, threshold=0.01)
    # unfold = torch.nn.Unfold(3, stride=2)
    # x = torch.rand(4,1,320000)
    # frame_len = 320
    # print(x)
    # x = F.pad(x, (frame_len, frame_len), "constant", 0)
    # print(x)
    # x = x.unfold(-1, frame_len * 3, frame_len)
    # print(x.shape)
    # x = estimate(x, 16000)
    # print(x.shape)
    # x = x.squeeze(-1)
    # print(x.shape)


    # print(x)
    # print(x.unfold(-1, ))
    # x = torch.unfold()
    # print(a.shape)