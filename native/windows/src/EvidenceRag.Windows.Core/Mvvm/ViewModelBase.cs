using System.Text.Json;

namespace EvidenceRag.Windows.Core.Mvvm;

public abstract class ViewModelBase : ObservableObject, IDisposable
{
    private CancellationTokenSource? _operation;
    private bool _isBusy;
    private string _errorMessage = "";

    public bool IsBusy { get => _isBusy; protected set => SetProperty(ref _isBusy, value); }
    public string ErrorMessage { get => _errorMessage; protected set => SetProperty(ref _errorMessage, value); }
    public bool HasError => !string.IsNullOrEmpty(ErrorMessage);

    protected async Task RunLatestAsync(Func<CancellationToken, Task> operation)
    {
        _operation?.Cancel();
        _operation?.Dispose();
        var current = new CancellationTokenSource();
        _operation = current;
        IsBusy = true;
        ErrorMessage = "";
        RaisePropertyChanged(nameof(HasError));
        try
        {
            await operation(current.Token).ConfigureAwait(true);
        }
        catch (OperationCanceledException) when (current.IsCancellationRequested) { }
        catch (Exception exception)
        {
            ErrorMessage = ToSafeMessage(exception);
            RaisePropertyChanged(nameof(HasError));
        }
        finally
        {
            if (ReferenceEquals(_operation, current))
            {
                _operation = null;
                IsBusy = false;
            }
            current.Dispose();
        }
    }

    public void Cancel() => _operation?.Cancel();
    public void Dispose() { _operation?.Cancel(); _operation?.Dispose(); GC.SuppressFinalize(this); }

    private static string ToSafeMessage(Exception exception) => exception switch
    {
        Api.ApiFailureException failure => failure.Message,
        HttpRequestException => "无法连接本机 RAG 服务，请确认服务已启动。",
        JsonException => "服务返回了无法识别的数据。",
        _ => "操作失败；为避免泄露敏感信息，详细内容未显示。",
    };
}
