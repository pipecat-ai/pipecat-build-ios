#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN
@interface PCPythonRuntime : NSObject
- (void)startWithEventHandler:(void (^)(NSString *json))handler;
- (void)sendJSON:(NSString *)json;
@end
NS_ASSUME_NONNULL_END
